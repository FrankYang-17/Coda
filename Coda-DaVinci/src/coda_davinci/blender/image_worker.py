"""Executed inside Blender to render one still image of the current scene."""

import sys
from pathlib import Path
import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from coda_davinci.blender.blender_gpu import configure_scene, inspect_graphics_device


def main():
    output = Path(sys.argv[sys.argv.index("--") + 1]).resolve()
    output.mkdir(parents=True, exist_ok=True)
    scene = bpy.context.scene
    configure_scene(scene)
    scene.render.image_settings.file_format = "PNG"
    scene.render.use_file_extension = True
    scene.render.filepath = str(output / "image.png")
    result = bpy.ops.render.render(write_still=True)
    if "FINISHED" not in result:
        raise RuntimeError(f"Still render did not finish: {result}")
    if not (output / "image.png").is_file():
        raise RuntimeError("Blender reported a completed render but image.png was not written")
    inspect_graphics_device(scene)


if __name__ == "__main__":
    main()
