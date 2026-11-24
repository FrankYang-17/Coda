import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from coda_davinci.blender.scene_inspection_worker import inspect
from coda_davinci.blender.camera_preview_worker import preview, save_selected


def main():
    path = Path(sys.argv[sys.argv.index("--") + 1])
    request = json.loads(path.read_text())
    if request["mode"] == "inspect":
        result = inspect(request)
    elif request["mode"] == "preview":
        result = preview(request, path.parent)
    elif request["mode"] == "apply":
        result = save_selected(request)
    else:
        raise ValueError("Unknown scene tool operation")
    (path.parent / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
