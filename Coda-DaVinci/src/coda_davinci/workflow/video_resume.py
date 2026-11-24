"""Recover a video job without accidentally creating a second generation."""

import json
from pathlib import Path


def read_record(path: Path) -> dict:
    return json.loads(path.read_text()) if path.exists() else {}


def submission_rejected(job: Path) -> bool:
    """Recognize a recorded billing refusal, never an unknown POST outcome.

    Older runs only persisted the HTTP response in the exception message. Keep
    this allowlist narrow: a generic 429, transport error, or missing ID is not
    proof that the provider did not create a task.
    """
    meta = job / ".harness"
    result_path = meta / "result.json"
    result = read_record(result_path)
    phase = read_record(meta / "phase.json")
    if (
        result.get("status") != "error"
        or result.get("provider") != "seedance"
        or result.get("phase") != "submitting"
        or (phase.get("phase") != "submitting")
    ):
        return False
    if (
        result.get("task_id")
        or phase.get("task_id")
        or read_record(meta / "retry_state.json").get("task_id")
        or read_record(meta / "create_response.json")
        or any(meta.glob("poll_*.json"))
    ):
        return False
    request = meta / "request.json"
    if not request.is_file():
        return False
    markers = [request, meta / "phase.json", meta / "submission_attempt.json"]
    if any((p.exists() and p.stat().st_mtime_ns > result_path.stat().st_mtime_ns for p in markers)):
        return False
    prefix = "Klink POST /v1/videos/generations returned HTTP 429: "
    message = result.get("message", "")
    if not isinstance(message, str) or not message.startswith(prefix):
        return False
    try:
        response = json.loads(message[len(prefix) :])
    except json.JSONDecodeError:
        return False
    return (
        isinstance(response, dict)
        and (not response.get("id"))
        and (not response.get("task_id"))
        and (response.get("klink_error_code") == "credit_balance_exhausted")
        and (response.get("retryable") is False)
    )


def generation_rejected(job: Path) -> bool:
    if submission_rejected(job):
        return True
    meta = job / ".harness"
    latest = max(meta.glob("poll_*.json"), key=lambda path: path.stat().st_mtime_ns, default=None)
    record = read_record(latest if latest is not None else meta / "create_response.json")
    return bool(record.get("id")) and record.get("status") in {
        "failed",
        "cancelled",
        "canceled",
        "expired",
        "error",
    }


def resume_task_id(job: Path) -> str | None:
    meta = job / ".harness"
    result = read_record(meta / "result.json")
    created = read_record(meta / "create_response.json")
    phase_record = read_record(meta / "phase.json")
    task_id = result.get("task_id") or created.get("id") or phase_record.get("task_id")
    if task_id:
        return task_id
    if submission_rejected(job):
        return None
    if (meta / "request.json").exists():
        phase = phase_record.get("phase") or result.get("phase")
        legacy_upload_failure = (
            phase is None
            and result.get("status") == "error"
            and str(result.get("message", "")).startswith("Klink media upload")
        )
        if created or not (phase == "uploading" or legacy_upload_failure):
            raise ValueError(
                f"{job}: previous submission has no task ID; inspect it before retrying to avoid duplicate generation"
            )
    return None
