import json
from coda_davinci.media.video_media import save


def save_original(workspace, prompt, references, videos, round_index=0):
    path = workspace / ".harness" / "task_inputs" / str(round_index) / "input.json"
    record = {
        "prompt": prompt,
        "images": [
            {"label": r.label, "path": r.path.relative_to(workspace).as_posix()} for r in references
        ],
        "videos": [
            {"label": r.label, "path": r.path.relative_to(workspace).as_posix()} for r in videos
        ],
    }
    if path.exists() and json.loads(path.read_text()) != record:
        raise ValueError("Original task input changed for this interaction round")
    save(path, record)
