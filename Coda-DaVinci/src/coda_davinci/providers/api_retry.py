"""Retry one model turn without changing history or replaying tool execution."""

import json
import logging
from pathlib import Path
import threading
import time
import httpx

LOG = logging.getLogger(__name__)
RETRY_DELAYS = (2, 4, 8, 16, 30, 60, 60, 60)
PERMANENT_CODES = {
    "invalid_api_key",
    "authentication_error",
    "permission_error",
    "invalid_request_error",
    "invalid_prompt",
    "context_length_exceeded",
    "content_policy_violation",
    "insufficient_quota",
}
TRANSIENT_CODES = {
    "server_error",
    "internal_server_error",
    "api_error",
    "overloaded_error",
    "upstream_error",
    "rate_limit_error",
    "rate_limit_exceeded",
}


class RetryableResponseError(RuntimeError):
    """The upstream returned a failed, malformed or unfinished response."""


def get_request_id(value):
    for name in ("request_id", "_request_id"):
        identifier = getattr(value, name, None)
        if isinstance(identifier, str) and identifier:
            return identifier
    response = getattr(value, "response", value)
    headers = getattr(response, "headers", {})
    return (
        headers.get("x-request-id") or headers.get("request-id") or headers.get("apim-request-id")
    )


def with_request_id(error, response):
    error.request_id = get_request_id(error) or get_request_id(response)
    return error


def retry_limit(protocol: str, configured: int | None = None) -> int:
    """Honor explicit budgets; use the existing default for every provider."""
    return len(RETRY_DELAYS) if configured is None else configured


def is_retryable(exc: Exception, api_error_type: type[Exception]) -> bool:
    if isinstance(
        exc,
        (
            RetryableResponseError,
            httpx.TransportError,
            ConnectionError,
            TimeoutError,
            json.JSONDecodeError,
        ),
    ):
        return True
    if not isinstance(exc, api_error_type):
        return False
    body = getattr(exc, "body", None)
    error = body.get("error", body) if isinstance(body, dict) else {}
    code = error.get("code") or error.get("type") if isinstance(error, dict) else None
    code = code or getattr(exc, "code", None)
    code = code if isinstance(code, str) else None
    status = getattr(exc, "status_code", None)
    if status in {401, 403, 404, 422} or code in PERMANENT_CODES:
        return False
    if code in TRANSIENT_CODES:
        return True
    return status is None or (
        isinstance(status, int) and (status == 200 or status in {408, 409, 429} or status >= 500)
    )


def validate_response(response, protocol: str):
    try:
        _validate_response(response, protocol)
    except Exception as exc:
        raise with_request_id(exc, response)


def _validate_response(response, protocol: str):
    if not isinstance(getattr(response, "id", None), str):
        raise RetryableResponseError("Upstream response is missing its response ID")
    if protocol == "responses":
        status = getattr(response, "status", None)
        error = getattr(response, "error", None)
        code = error.get("code") if isinstance(error, dict) else getattr(error, "code", None)
        if status == "failed":
            if isinstance(code, str) and code in PERMANENT_CODES:
                raise RuntimeError(f"Response {response.id} failed: {error}")
            raise RetryableResponseError(f"Response {response.id} failed: {error}")
        if status == "incomplete":
            raise RuntimeError(f"Response {response.id} incomplete: {response.incomplete_details}")
        if status != "completed" or not isinstance(getattr(response, "output", None), list):
            raise RetryableResponseError(
                f"Response {response.id} is not complete or is malformed: {status}"
            )
    elif protocol == "chat":
        choices = getattr(response, "choices", None)
        if not isinstance(choices, list) or len(choices) != 1:
            raise RetryableResponseError(f"Completion {response.id}: expected one complete choice")
        choice = choices[0]
        reason = getattr(choice, "finish_reason", None)
        if reason is None or getattr(choice, "message", None) is None:
            raise RetryableResponseError(
                f"Completion {response.id}: missing final message or finish_reason"
            )
        if reason not in {"stop", "tool_calls"}:
            raise RuntimeError(f"Completion {response.id}: {reason}")
    elif protocol == "anthropic":
        reason = getattr(response, "stop_reason", None)
        if reason is None or not isinstance(getattr(response, "content", None), list):
            raise RetryableResponseError(
                f"Message {response.id}: missing final content or stop_reason"
            )
        if reason not in {"end_turn", "tool_use", "stop_sequence"}:
            raise RuntimeError(f"Message {response.id}: {reason}")
    else:
        raise ValueError(f"Unknown model protocol: {protocol}")


def request_with_retry(
    call, *, tools, turn_dir: Path, api_error_type: type[Exception], protocol: str
):
    cancelled = tools.context.team.cancelled if tools.context else threading.Event()

    def check_cancelled():
        if tools.context:
            tools.context.check_cancelled()
        if cancelled.is_set():
            raise RuntimeError("Task tree cancelled")

    configured = getattr(getattr(tools, "settings", None), "api_max_retries", None)
    max_retries = len(RETRY_DELAYS) if configured is None else configured
    for attempt in range(max_retries + 1):
        check_cancelled()
        started = time.monotonic()
        try:
            with tools.model_request():
                response = call()
            check_cancelled()
            validate_response(response, protocol)
        except Exception as exc:
            retry = is_retryable(exc, api_error_type) and attempt < max_retries
            delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)] if retry else None
            record = {
                "attempt": attempt + 1,
                "status": "error",
                "type": type(exc).__name__,
                "message": str(exc),
                "status_code": getattr(exc, "status_code", None),
                "request_id": get_request_id(exc),
                "elapsed_seconds": round(time.monotonic() - started, 2),
                "retry_after_seconds": delay,
            }
            with (turn_dir / "api_attempts.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
            if not retry:
                LOG.error(
                    "Model request failed (%s); request_id=%s; attempt=%d; details=%s",
                    type(exc).__name__,
                    record["request_id"],
                    attempt + 1,
                    turn_dir,
                )
                raise
            LOG.warning(
                "Model request failed (%s); request_id=%s; retry %d/%d in %ds; details=%s",
                type(exc).__name__,
                record["request_id"],
                attempt + 1,
                max_retries,
                delay,
                turn_dir,
            )
            deadline = time.monotonic() + delay
            while time.monotonic() < deadline:
                check_cancelled()
                cancelled.wait(min(0.25, max(0, deadline - time.monotonic())))
        else:
            record = {
                "attempt": attempt + 1,
                "status": "success",
                "elapsed_seconds": round(time.monotonic() - started, 2),
            }
            with (turn_dir / "api_attempts.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record) + "\n")
            return response
