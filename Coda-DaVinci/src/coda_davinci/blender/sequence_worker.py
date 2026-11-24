"""Executed inside Blender after the agent's scene and render setup scripts."""

import json
import sys
from pathlib import Path
import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from coda_davinci.blender.blender_gpu import configure_scene, inspect_graphics_device


def main():
    output = Path(sys.argv[sys.argv.index("--") + 1]).resolve()
    frames_dir = output / "frames"
    frames_dir.mkdir()
    scene = bpy.context.scene
    configure_scene(scene)
    scene.render.image_settings.file_format = "PNG"
    scene.render.use_file_extension = True
    scene.render.filepath = str(frames_dir / "frame_####")
    frames = [
        str(Path(scene.render.frame_path(frame=frame)).relative_to(output))
        for frame in range(scene.frame_start, scene.frame_end + 1, scene.frame_step)
    ]
    manifest = {
        "frames": frames,
        "fps": scene.render.fps / scene.render.fps_base / scene.frame_step,
        "scene_checkpoint": "scene.blend",
    }
    bpy.ops.wm.save_as_mainfile(filepath=str(output / "scene.blend"), copy=True)
    result = bpy.ops.render.render(animation=True)
    if "FINISHED" not in result:
        raise RuntimeError(f"Animation render did not finish: {result}")
    inspect_graphics_device(scene)
    (output / "sequence.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
