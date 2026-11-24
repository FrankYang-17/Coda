from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import re
import shutil
import uuid
from coda_davinci.agents.agent import save_json
from .video_retry import DELAYS, VideoAPIError, api_keys, backoff, retryable

LOG = logging.getLogger(__name__)


def record(path):
    return json.loads(path.read_text()) if path.exists() else {}


def run_with_keys(run_once, client_class, settings, workspace, prompt, **kwargs):
    workspace = Path(workspace).resolve()
    meta = Path(kwargs.get("meta_dir") or workspace / ".harness").resolve()
    if not meta.is_relative_to(workspace):
        raise ValueError("Video metadata must stay in the workspace")
    meta.mkdir(parents=True, exist_ok=True)
    state_path = meta / "retry_state.json"
    state = record(state_path)
    keys = api_keys(settings.api_key)
    if not keys:
        raise ValueError("Set VIDEO_MODEL_API_KEY")
    client = kwargs.get("client") or client_class(settings)
    kwargs["client"] = client
    task_id = kwargs.get("task_id") or state.get("task_id")
    original = record(meta / "request.json")
    if task_id:
        if not kwargs.get("reference_video"):
            kwargs["reference_video"] = [
                workspace / p for p in original.get("reference_videos", [])
            ]
        if not kwargs.get("reference_images"):
            kwargs["reference_images"] = [
                (r["label"], workspace / r["path"]) for r in original.get("reference_images", [])
            ]
    index = state.get("key_index", 0)
    if index >= len(keys):
        raise ValueError("Saved video key index is absent from the current key list")
    client._managed_retry = True
    client.api_keys = keys
    client.idempotency_key = state.get("idempotency_key") or str(uuid.uuid4())
    history = meta / "api_attempts.jsonl"
    number = len(history.read_text().splitlines()) if history.exists() else 0
    check = kwargs.get("check_cancelled")
    try:
        for key_index in range(index, len(keys)):
            client.key_index = key_index
            client.api_key = keys[key_index]
            for attempt in range(len(DELAYS) + 1):
                if check:
                    check()
                number += 1
                save_json(
                    state_path,
                    {
                        "key_index": key_index,
                        "task_id": task_id,
                        "idempotency_key": client.idempotency_key,
                    },
                )
                kwargs["task_id"] = task_id
                try:
                    result = run_once(settings, workspace, prompt, **kwargs)
                except Exception as exc:
                    if not isinstance(exc, VideoAPIError) and (not retryable(exc)):
                        raise
                    phase = record(meta / "phase.json")
                    task_id = phase.get("task_id") or task_id
                    status = getattr(exc, "provider_status", None)
                    message = str(exc)
                    for key in keys:
                        message = message.replace(key, "[API_KEY]")
                    message = re.sub("https?://[^\\s\\\"\\'<>]+", "[URL]", message)
                    entry = {
                        "time": datetime.now(timezone.utc).isoformat(),
                        "attempt": number,
                        "key_index": key_index + 1,
                        "key_attempt": attempt + 1,
                        "phase": phase.get("phase"),
                        "task_id": task_id,
                        "request_id": getattr(exc, "request_id", None)
                        or getattr(client, "last_request_id", None),
                        "error_type": type(exc).__name__,
                        "error": message,
                    }
                    with history.open("a") as stream:
                        stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
                    LOG.warning(
                        "Video API failed | key=%s/%s attempt=%s/6 phase=%s task_id=%s request_id=%s error=%s",
                        key_index + 1,
                        len(keys),
                        attempt + 1,
                        entry["phase"],
                        task_id,
                        entry["request_id"],
                        " ".join(message.split())[:600],
                    )
                    archive = meta / "retry_attempts" / f"{number:04d}"
                    archive.mkdir(parents=True, exist_ok=True)
                    for p in meta.glob("*.json"):
                        if p.name != "retry_state.json":
                            shutil.copy2(p, archive / p.name)
                    if getattr(settings, "fail_fast_permanent_errors", False):
                        if getattr(exc, "privacy_rejection", False):
                            raise
                        if (
                            re.search("\\bHTTP 401\\b", str(exc))
                            or getattr(exc, "status_code", None) == 401
                        ):
                            if task_id or key_index == len(keys) - 1:
                                raise
                            save_json(
                                state_path,
                                {
                                    "key_index": key_index + 1,
                                    "task_id": None,
                                    "idempotency_key": client.idempotency_key,
                                },
                            )
                            break
                    if not retryable(exc) or (
                        attempt == len(DELAYS) and key_index == len(keys) - 1
                    ):
                        raise
                    if status in {"failed", "cancelled", "canceled", "expired", "error"}:
                        task_id = None
                        client.idempotency_key = str(uuid.uuid4())
                        for p in meta.glob("poll_*.json"):
                            p.unlink()
                        (meta / "create_response.json").unlink(missing_ok=True)
                    save_json(
                        state_path,
                        {
                            "key_index": key_index,
                            "task_id": task_id,
                            "idempotency_key": client.idempotency_key,
                        },
                    )
                    if attempt < len(DELAYS):
                        backoff(DELAYS[attempt], check)
                else:
                    save_json(
                        state_path,
                        {
                            "key_index": key_index,
                            "task_id": result.get("task_id"),
                            "idempotency_key": client.idempotency_key,
                        },
                    )
                    return result
    finally:
        client._managed_retry = False
