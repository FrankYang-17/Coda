"""Select a visible CUDA device for Blender rendering."""

import os
from pathlib import Path
import sys
import traceback


def cuda_cache_directory() -> Path:
    """Resolve the host cache before the sandbox replaces HOME with a temporary path."""
    path = Path(os.environ.get("CUDA_CACHE_PATH") or Path.home() / ".nv" / "ComputeCache")
    path = path.expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def normalize_engine(engine: str) -> str:
    if not isinstance(engine, str) or not engine.strip():
        raise ValueError("engine must be a nonempty Blender engine identifier")
    value = engine.strip().upper()
    if value != "CYCLES":
        raise ValueError("Only CYCLES rendering is enabled")
    return value


def graphics_arguments(engine: str) -> list[str]:
    normalize_engine(engine)
    return []


def bootstrap_expression() -> str:
    root = str(Path(__file__).resolve().parents[2])
    return f"import sys; sys.path.insert(0, {root!r}); from coda_davinci.blender.blender_gpu import install; install()"


def configure_scene(scene):
    import bpy

    (rank, size) = (0, 1)
    if scene.render.engine == "CYCLES":
        prefs = bpy.context.preferences.addons["cycles"].preferences
        prefs.compute_device_type = "CUDA"
        prefs.get_devices()
        devices = [device for device in prefs.devices if device.type == "CUDA"]
        if not devices:
            raise RuntimeError("No visible CUDA devices found")
        device_index = rank % len(devices)
        selected = devices[device_index]
        for device in prefs.devices:
            device.use = device.type == "CUDA" and device.id == selected.id
        scene.cycles.device = "GPU"
        print(
            f"GPU_SELECTION rank={rank}/{size} backend=CUDA index={device_index} name={selected.name} id={selected.id}; CPU disabled",
            flush=True,
        )
    else:
        raise RuntimeError('Only CYCLES rendering is enabled; set scene.render.engine = "CYCLES"')


def inspect_graphics_device(scene):
    if scene.render.engine == "CYCLES":
        return
    import gpu

    backend = gpu.platform.backend_type_get()
    vendor = gpu.platform.vendor_get()
    renderer = gpu.platform.renderer_get()
    print(f"Graphics backend: {backend}; vendor: {vendor}; renderer: {renderer}", flush=True)
    software = ("llvmpipe", "lavapipe", "softpipe", "swrast", "swiftshader", "software rasterizer")
    if backend != "VULKAN" or any((name in f"{vendor} {renderer}".lower() for name in software)):
        raise RuntimeError(
            "Requested hardware Vulkan rendering was replaced by another backend or software renderer"
        )
    if not renderer or renderer.lower() in {"unknown", "none"}:
        raise RuntimeError("Cannot identify the graphics renderer")


def _render_init(scene, *_):
    import bpy

    try:
        configure_scene(scene)
        for other in bpy.data.scenes:
            if other != scene and other.render.engine == "CYCLES":
                configure_scene(other)
    except Exception:
        traceback.print_exc()
        sys.stderr.flush()
        os._exit(1)


def _render_post(scene, *_):
    try:
        inspect_graphics_device(scene)
    except Exception:
        traceback.print_exc()
        sys.stderr.flush()
        os._exit(1)


def install():
    import bpy
    from bpy.app.handlers import persistent

    for handlers, callback in (
        (bpy.app.handlers.render_init, _render_init),
        (bpy.app.handlers.render_post, _render_post),
    ):
        if callback not in handlers:
            handlers.append(persistent(callback))
