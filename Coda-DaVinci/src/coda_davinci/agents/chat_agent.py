"""OpenAI Chat Completions with local message history and workspace tools."""

import json
import logging
import time
from pathlib import Path
import httpx
from types import SimpleNamespace
from openai import APIError, APIConnectionError, APITimeoutError
from coda_davinci.agents.agent import save_json
from coda_davinci.providers.api_retry import (
    request_with_retry,
    RetryableResponseError,
    validate_response,
    with_request_id,
    get_request_id,
)
from coda_davinci.agents.context_compaction import ContextCompressor
from coda_davinci.agents.model_stream import chat_stream
from coda_davinci.runtime.config import Settings
from coda_davinci.agents.conversation import prepare_conversation, session_identity
from coda_davinci.tools.workspace import ToolResult, WorkspaceTools, image_input
from coda_davinci.agents.tool_dispatch import execute_calls
from coda_davinci.agents.agent_progress import IdleTurnGuard

LOG = logging.getLogger(__name__)


class IncompleteChatStreamError(RetryableResponseError):
    """The gateway ended an SSE response before its terminal chunk."""


def collect_streamed_chat_completion(stream) -> tuple[str, str | None, str, dict]:
    """Collect one streamed prompt-tool response into an auditable result."""
    chunks = []
    content = []
    response_id = None
    finish_reason = None
    for chunk in stream:
        chunks.append(chunk.model_dump(mode="json"))
        response_id = response_id or getattr(chunk, "id", None)
        choices = getattr(chunk, "choices", [])
        if len(choices) > 1:
            raise RuntimeError("Expected at most one choice in each streamed chunk")
        if not choices:
            continue
        choice = choices[0]
        piece = getattr(choice.delta, "content", None)
        if piece:
            content.append(piece)
        if choice.finish_reason is not None:
            finish_reason = choice.finish_reason
    if finish_reason is None:
        raise IncompleteChatStreamError(
            "Chat stream ended before a terminal finish_reason was received"
        )
    raw_content = "".join(content)
    audit = {
        "stream": True,
        "id": response_id,
        "request_id": get_request_id(stream),
        "finish_reason": finish_reason,
        "content": raw_content,
        "chunks": chunks,
    }
    return (raw_content, response_id, finish_reason, audit)


def create_streamed_chat_completion(client, request: dict, *, max_retries: int):
    """Retry safely if a streamed response is interrupted before tool execution."""
    retryable = (
        APIConnectionError,
        APITimeoutError,
        httpx.TransportError,
        IncompleteChatStreamError,
    )
    for attempt in range(max_retries + 1):
        stream = None
        try:
            stream = client.chat.completions.create(**{**request, "stream": True})
            return collect_streamed_chat_completion(stream)
        except retryable as exc:
            with_request_id(exc, stream)
            if attempt >= max_retries:
                raise
            delay = min(0.5 * 2**attempt, 8.0)
            LOG.warning(
                "Chat stream interrupted (%s); request_id=%s; retrying %d/%d in %.1fs",
                type(exc).__name__,
                exc.request_id,
                attempt + 1,
                max_retries,
                delay,
            )
            time.sleep(delay)
        except Exception as exc:
            raise with_request_id(exc, stream)
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                close()


def prompt_tool_protocol(schemas: list[dict]) -> str:
    definitions = [schema["function"] for schema in schemas]
    return (
        '\n\nThe API gateway cannot transport native function calls. Use this exact JSON\ntool protocol instead. Every assistant response must be one JSON object, with\nno Markdown fence and no text outside the object:\n{"assistant_text":"brief progress note","tool_calls":[{"name":"tool_name","arguments":{}}]}\n\ntool_calls must be a JSON array. Independent tools may be batched in one response. arguments\nmust be a JSON object matching that tool\'s schema. To finish, call the available finish or submit_task tool alone in\ntool_calls; do not merely describe completion. If no tool is appropriate yet,\nreturn an empty tool_calls array and explain the next step in assistant_text.\n\nAvailable tool definitions:\n'
        + json.dumps(definitions, ensure_ascii=False, separators=(",", ":"))
    )


def parse_prompt_tool_message(content: str) -> tuple[str, list[dict]]:
    if not isinstance(content, str) or not content.strip():
        raise ValueError("Prompt tool response must contain a JSON object")
    text = content.strip()
    if "</think>" in text:
        text = text.rsplit("</think>", 1)[1].strip()
    if text.startswith("```"):
        first_newline = text.find("\n")
        text = text[first_newline + 1 :] if first_newline >= 0 else text[3:]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3].rstrip()
    start = text.find("{")
    if start < 0:
        raise ValueError("Prompt tool response did not contain a JSON object")
    try:
        (value, _) = json.JSONDecoder().raw_decode(text[start:])
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid prompt tool JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("Prompt tool response must be a JSON object")
    assistant_text = value.get("assistant_text", "")
    if not isinstance(assistant_text, str):
        raise ValueError("assistant_text must be a string")
    calls = value.get("tool_calls", [])
    if not isinstance(calls, list):
        raise ValueError("tool_calls must be an array")
    normalized = []
    for call in calls:
        if not isinstance(call, dict) or not isinstance(call.get("name"), str):
            raise ValueError("Each prompt tool call requires a string name")
        arguments = call.get("arguments", {})
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid tool arguments JSON: {exc}") from exc
        if not isinstance(arguments, dict):
            raise ValueError("Tool call arguments must be a JSON object")
        normalized.append({"name": call["name"], "arguments": arguments})
    return (assistant_text, normalized)


def chat_tool_outputs(call, result: ToolResult) -> tuple[dict, list[dict]]:
    output = {
        "role": "tool",
        "tool_call_id": call.id,
        "content": json.dumps(result.data, ensure_ascii=False),
    }
    images = []
    for viewed in result.images:
        images.extend(
            [
                {
                    "type": "text",
                    "text": f"{call.function.name}, tool_call_id={call.id}: {viewed.label}",
                },
                {
                    "type": "image_url",
                    "image_url": {"url": image_input(viewed.path)["image_url"], "detail": "high"},
                },
            ]
        )
    return (output, images)


def run_chat_agent(client, settings: Settings, workspace: Path, goal: str, context=None) -> dict:
    tools = WorkspaceTools(workspace, settings, context, goal=goal)
    schemas = [
        {
            "type": "function",
            "function": {key: value for (key, value) in tool.items() if key != "type"},
        }
        for tool in tools.schemas
    ]
    if getattr(settings, "chat_tool_mode", "native") == "prompt":
        return run_prompt_tool_agent(client, settings, workspace, goal, tools, schemas)
    prompt = tools.prompt
    effort_options = (
        {"reasoning_effort": settings.reasoning_effort}
        if settings.reasoning_effort is not None
        else {}
    )
    (history, first_turn) = prepare_conversation(tools, goal, "chat")
    compressor = ContextCompressor(tools, settings, history, goal, "chat")

    def summarize(system, text):
        response = chat_stream(
            client,
            model=settings.model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": text}],
            max_completion_tokens=8192,
            **effort_options,
        )
        validate_response(response, "chat")
        return response.choices[0].message.content

    save_json(
        tools.meta / "request.json",
        {
            "provider": "openai",
            "openai_api": "chat",
            "chat_tool_mode": "native",
            "goal": goal,
            "stream": not settings.model.startswith("gemini-"),
            "transport_protocol": "gemini_generate_content"
            if settings.model.startswith("gemini-")
            else "openai_chat_completions",
            **session_identity(context),
            "model": settings.model,
            "instructions": prompt,
            "tools": schemas,
            "max_turns": settings.max_turns,
            **effort_options,
            "sandbox": {"enabled": settings.sandbox, "bwrap": str(settings.bwrap)},
        },
    )
    final = {"status": "iteration_limit_reached"}
    progress = IdleTurnGuard()
    last_response_id = None
    try:
        for step in range(1, settings.max_turns + 1):
            turn = first_turn + step - 1
            turn_dir = tools.meta / f"turn_{turn:04d}"
            turn_dir.mkdir()
            history.extend(tools.background_messages())
            request = dict(
                model=settings.model,
                messages=history,
                tools=schemas,
                parallel_tool_calls=True,
                tool_choice="auto",
                store=False,
                **effort_options,
            )
            save_json(turn_dir / "input.json", request)
            LOG.info(
                "Model request %d/%d (Chat Completions); audit turn %d",
                step,
                settings.max_turns,
                turn,
            )
            started = time.monotonic()

            def call_model():
                history.extend(tools.background_messages())
                return compressor.call(
                    request,
                    turn_dir,
                    lambda value: chat_stream(client, **value),
                    summarize,
                    lambda response: validate_response(response, "chat"),
                )

            response = request_with_retry(
                call_model, tools=tools, turn_dir=turn_dir, api_error_type=APIError, protocol="chat"
            )
            save_json(turn_dir / "response.json", response.model_dump(mode="json"))
            LOG.info("Model output complete (%.1fs)", time.monotonic() - started)
            last_response_id = response.id
            choice = response.choices[0]
            message = choice.message
            calls = message.tool_calls or []
            if any((call.type != "function" for call in calls)):
                raise ValueError("Only function tool calls are supported")
            history.append(message.model_dump(mode="json", exclude_none=True))
            if message.content:
                LOG.info("Agent: %s", message.content)
            stalled = progress.observe(
                has_calls=bool(calls), has_active_jobs=tools.jobs.has_active(), text=message.content
            )
            if stalled:
                final = stalled
                save_json(turn_dir / "tool_outputs.json", [])
                LOG.warning("%s", final["message"])
                break
            (pending, images) = ([], [])
            results = execute_calls(
                tools, [(call.function.name, call.function.arguments) for call in calls]
            )
            for call, result in zip(calls, results):
                (output, viewed) = chat_tool_outputs(call, result)
                pending.append(output)
                images.extend(viewed)
                if result.finished:
                    final = result.data
            if images:
                pending.append({"role": "user", "content": images})
            if not calls:
                pending.append(
                    {
                        "role": "user",
                        "content": "Continue using the enabled tools. " + tools.completion_hint,
                    }
                )
            save_json(turn_dir / "tool_outputs.json", pending)
            history.extend(pending)
            if not calls and tools.jobs.has_active():
                history.extend(tools.background_messages(wait=True))
            if final["status"] == "completed":
                break
    except KeyboardInterrupt:
        final = {"status": "interrupted"}
        raise
    except Exception as exc:
        final = {"status": "error", "type": type(exc).__name__, "message": str(exc)}
        raise
    finally:
        tools.close()
        history.extend(tools.background_messages())
        save_json(tools.meta / "history.json", history)
        save_json(tools.meta / "result.json", {**final, "last_response_id": last_response_id})
    return final


def run_prompt_tool_agent(
    client,
    settings: Settings,
    workspace: Path,
    goal: str,
    tools: WorkspaceTools,
    schemas: list[dict],
) -> dict:
    """Chat loop for gateways that reject native function-calling requests."""
    prompt = tools.prompt + prompt_tool_protocol(schemas)
    tools.prompt = prompt
    effort_options = (
        {"reasoning_effort": settings.reasoning_effort}
        if settings.reasoning_effort is not None
        else {}
    )
    (history, first_turn) = prepare_conversation(tools, goal, "chat")
    compressor = ContextCompressor(tools, settings, history, goal, "chat")

    def send(request):
        (text, response_id, finish_reason, audit) = create_streamed_chat_completion(
            client, request, max_retries=0
        )
        usage = next(
            (chunk["usage"] for chunk in reversed(audit["chunks"]) if chunk.get("usage")), None
        )
        return SimpleNamespace(
            id=response_id,
            _request_id=audit["request_id"],
            audit=audit,
            usage=usage,
            choices=[
                SimpleNamespace(finish_reason=finish_reason, message=SimpleNamespace(content=text))
            ],
        )

    def summarize(system, text):
        result = send(
            dict(
                model=settings.model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": text}],
                max_tokens=8192,
                **effort_options,
            )
        )
        validate_response(result, "chat")
        return result.choices[0].message.content

    save_json(
        tools.meta / "request.json",
        {
            "provider": "openai",
            "openai_api": "chat",
            "chat_tool_mode": "prompt",
            "goal": goal,
            "model": settings.model,
            "instructions": prompt,
            **session_identity(tools.context),
            "tools": schemas,
            "max_turns": settings.max_turns,
            "api_max_retries": settings.api_max_retries,
            **effort_options,
            "sandbox": {"enabled": settings.sandbox, "bwrap": str(settings.bwrap)},
        },
    )
    final = {"status": "iteration_limit_reached"}
    progress = IdleTurnGuard()
    last_response_id = None
    try:
        for step in range(1, settings.max_turns + 1):
            turn = first_turn + step - 1
            turn_dir = tools.meta / f"turn_{turn:04d}"
            turn_dir.mkdir()
            history.extend(tools.background_messages())
            request = dict(model=settings.model, messages=history, stream=True, **effort_options)
            save_json(turn_dir / "input.json", {**request, "stream": True})
            LOG.info("Model request %d/%d (Chat prompt tools, streaming)", turn, settings.max_turns)
            started = time.monotonic()

            def call_model():
                history.extend(tools.background_messages())
                return compressor.call(
                    request,
                    turn_dir,
                    send,
                    summarize,
                    lambda response: validate_response(response, "chat"),
                )

            response = request_with_retry(
                call_model, tools=tools, turn_dir=turn_dir, api_error_type=APIError, protocol="chat"
            )
            raw_content = response.choices[0].message.content
            (response_id, finish_reason) = (response.id, response.choices[0].finish_reason)
            response_audit = response.audit
            save_json(turn_dir / "response.json", response_audit)
            LOG.info("Model output complete (%.1fs)", time.monotonic() - started)
            last_response_id = response_id
            if finish_reason != "stop":
                raise RuntimeError(f"Completion {response_id}: {finish_reason}; see {turn_dir}")
            history.append({"role": "assistant", "content": raw_content})
            try:
                (assistant_text, calls) = parse_prompt_tool_message(raw_content)
            except ValueError as exc:
                LOG.warning("Invalid prompt tool response: %s", exc)
                stalled = progress.observe(
                    has_calls=False, has_active_jobs=tools.jobs.has_active(), text=raw_content
                )
                if stalled:
                    final = stalled
                    save_json(turn_dir / "tool_outputs.json", [])
                    LOG.warning("%s", final["message"])
                    break
                pending = [
                    {
                        "role": "user",
                        "content": f"Protocol error: {exc}. Reply again as exactly one valid JSON object with assistant_text and tool_calls.",
                    }
                ]
                save_json(turn_dir / "tool_outputs.json", pending)
                history.extend(pending)
                continue
            if assistant_text:
                LOG.info("Agent: %s", assistant_text)
            stalled = progress.observe(
                has_calls=bool(calls), has_active_jobs=tools.jobs.has_active(), text=assistant_text
            )
            if stalled:
                final = stalled
                save_json(turn_dir / "tool_outputs.json", [])
                LOG.warning("%s", final["message"])
                break
            pending_content = []
            outcomes = execute_calls(
                tools,
                [
                    (call["name"], json.dumps(call["arguments"], ensure_ascii=False))
                    for call in calls
                ],
            )
            for index, (call, result) in enumerate(zip(calls, outcomes)):
                name = call["name"]
                pending_content.append(
                    {
                        "type": "text",
                        "text": f"Tool result for {name}, prompt_call_id=turn-{turn}-{index}: {json.dumps(result.data, ensure_ascii=False)}",
                    }
                )
                for viewed in result.images:
                    pending_content.extend(
                        [
                            {"type": "text", "text": viewed.label},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": image_input(viewed.path)["image_url"],
                                    "detail": "high",
                                },
                            },
                        ]
                    )
                if result.finished:
                    final = result.data
            if not calls:
                pending_content.append(
                    {
                        "type": "text",
                        "text": "Continue using the available tools. Return the next tool call in the required JSON object. "
                        + tools.completion_hint,
                    }
                )
            pending = [{"role": "user", "content": pending_content}]
            save_json(turn_dir / "tool_outputs.json", pending)
            history.extend(pending)
            if not calls and tools.jobs.has_active():
                history.extend(tools.background_messages(wait=True))
            if final["status"] == "completed":
                break
    except KeyboardInterrupt:
        final = {"status": "interrupted"}
        raise
    except Exception as exc:
        final = {"status": "error", "type": type(exc).__name__, "message": str(exc)}
        raise
    finally:
        tools.close()
        history.extend(tools.background_messages())
        save_json(tools.meta / "history.json", history)
        save_json(tools.meta / "result.json", {**final, "last_response_id": last_response_id})
    return final
