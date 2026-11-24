import json
from pathlib import Path


def write_usage(episode):
    rows = []
    seen = set()
    for path in sorted(Path(episode).rglob("*.json")):
        if path.name in {"usage.json", "episode_result.json"} or "context" in path.parts:
            continue
        try:
            data = json.loads(path.read_text())
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        usage = data.get("usage") or data.get("usageMetadata")
        response_id = data.get("id") or data.get("responseId")
        if not usage or not response_id or response_id in seen:
            continue
        seen.add(response_id)
        rows.append(
            {
                "response_id": response_id,
                "model": data.get("model") or data.get("modelVersion"),
                "usage": usage,
                "source": path.relative_to(episode).as_posix(),
            }
        )
    (Path(episode) / "usage.json").write_text(json.dumps({"responses": rows}, indent=2))
