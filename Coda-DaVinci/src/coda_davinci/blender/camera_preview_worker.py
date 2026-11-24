from bisect import bisect_right
import json
import math
from pathlib import Path
import bpy
from mathutils import Quaternion, Vector
from coda_davinci.blender.blender_gpu import configure_scene
from coda_davinci.blender.scene_inspection_worker import active_camera, curves_for, frame_at


def capture(scene):
    (shots, current) = ([], None)
    cuts = {m.frame for m in scene.timeline_markers if m.camera}
    for frame in range(scene.frame_start, scene.frame_end + 1):
        scene.frame_set(frame)
        camera = active_camera(scene, frame)
        if current is None or current["camera"] != camera.name or frame in cuts:
            current = {"camera": camera.name, "start": frame, "samples": []}
            shots.append(current)
        evaluated = camera.evaluated_get(bpy.context.evaluated_depsgraph_get())
        (position, rotation, scale) = evaluated.matrix_world.decompose()
        current["samples"].append(
            {
                "position": list(position),
                "rotation": list(rotation),
                "scale": list(scale),
                "lens": evaluated.data.lens,
            }
        )
    return {
        "fps": scene.render.fps / scene.render.fps_base,
        "start": scene.frame_start,
        "end": scene.frame_end,
        "shots": shots,
    }


def progress(t, duration, pace):
    if pace == "steady":
        return t
    ramp = min(0.3 / max(duration, 1e-06), 0.25)
    if t < ramp:
        return t * t / (2 * ramp * (1 - ramp))
    if t > 1 - ramp:
        return 1 - (1 - t) ** 2 / (2 * ramp * (1 - ramp))
    return (t - ramp / 2) / (1 - ramp)


def distance_table(samples):
    distances = [0.0]
    for a, b in zip(samples, samples[1:]):
        distances.append(distances[-1] + (Vector(b["position"]) - Vector(a["position"])).length)
    if distances[-1] < 1e-08:
        distances = [0.0]
        for a, b in zip(samples, samples[1:]):
            distances.append(
                distances[-1]
                + Quaternion(a["rotation"]).rotation_difference(Quaternion(b["rotation"])).angle
            )
    return distances


def apply(scene, base, variant):
    for name in {shot["camera"] for shot in base["shots"]}:
        camera = bpy.data.objects[name]
        camera.data = camera.data.copy()
        camera.parent = None
        camera.constraints.clear()
        camera.animation_data_clear()
        camera.data.animation_data_clear()
        camera.rotation_mode = "QUATERNION"
    for shot in base["shots"]:
        camera = bpy.data.objects[shot["camera"]]
        samples = shot["samples"]
        distances = distance_table(samples)
        origin = Vector(samples[0]["position"])
        previous = None
        for index in range(len(samples)):
            (left, right, alpha) = (index, index, 0.0)
            if variant["pace"] != "original" and distances[-1] > 1e-08:
                t = index / max(1, len(samples) - 1)
                distance = progress(t, len(samples) / base["fps"], variant["pace"]) * distances[-1]
                left = min(len(samples) - 1, max(0, bisect_right(distances, distance) - 1))
                right = min(left + 1, len(samples) - 1)
                span = distances[right] - distances[left]
                alpha = (distance - distances[left]) / span if span > 0 else 0.0
            (a, b) = (samples[left], samples[right])
            position = Vector(a["position"]).lerp(Vector(b["position"]), alpha)
            camera.location = origin + (position - origin) * variant["distance_scale"]
            camera.location.z += variant["height_offset"]
            rotation = Quaternion(a["rotation"]).slerp(Quaternion(b["rotation"]), alpha)
            if previous is not None and previous.dot(rotation) < 0:
                rotation.negate()
            previous = rotation.copy()
            camera.rotation_quaternion = rotation
            camera.scale = a["scale"]
            camera.data.lens = (
                variant["lens_mm"]
                if variant["lens_mm"] is not None
                else a["lens"] * (1 - alpha) + b["lens"] * alpha
            )
            frame = shot["start"] + index
            for channel in ("location", "rotation_quaternion", "scale"):
                camera.keyframe_insert(channel, frame=frame)
            camera.data.keyframe_insert("lens", frame=frame)
    for name in {shot["camera"] for shot in base["shots"]}:
        camera = bpy.data.objects[name]
        for curve in curves_for(camera) + curves_for(camera.data):
            for key in curve.keyframe_points:
                key.interpolation = "LINEAR"


def preview(request, output):
    scene = bpy.context.scene
    bpy.ops.wm.save_as_mainfile(filepath=str(output / "base.blend"), copy=True)
    base = capture(scene)
    (output / "camera_base.json").write_text(json.dumps(base))
    duration = (base["end"] - base["start"] + 1) / base["fps"]
    times = request["timestamps_seconds"]
    for t in times:
        frame_at(scene, t)
    clip_start = request["clip_start_seconds"]
    frame_at(scene, clip_start)
    clip_end = min(duration, clip_start + request["clip_duration_seconds"])
    preview_fps = min(8.0, base["fps"])
    clip_times = [
        clip_start + i / preview_fps
        for i in range(math.ceil((clip_end - clip_start) * preview_fps - 1e-08))
    ]
    (width, height) = (scene.render.resolution_x, scene.render.resolution_y)
    scale = min(640 / width, 360 / height)
    scene.render.resolution_x = max(2, int(width * scale) // 2 * 2)
    scene.render.resolution_y = max(2, int(height * scale) // 2 * 2)
    scene.render.resolution_percentage = 100
    scene.cycles.samples = 4
    scene.cycles.use_denoising = True
    scene.render.use_persistent_data = True
    scene.render.image_settings.file_format = "PNG"
    scene.render.use_file_extension = True
    configure_scene(scene)
    baseline = {
        "name": "baseline",
        "distance_scale": 1.0,
        "height_offset": 0.0,
        "lens_mm": None,
        "pace": "original",
    }
    result = {
        "status": "success",
        "variants": [],
        "preview_fps": preview_fps,
        "clip_start_seconds": clip_start,
        "clip_duration_seconds": clip_end - clip_start,
        "timestamps_seconds": times,
        "sample_times_seconds": [
            (int(frame_at(scene, t)) - base["start"]) / base["fps"] for t in times
        ],
    }
    for number, variant in enumerate([baseline, *request["variants"]]):
        folder = output / f"variant_{number:02d}"
        folder.mkdir()
        row = {"name": variant["name"], "parameters": variant, "directory": folder.name}
        try:
            apply(scene, base, variant)
            frames = sorted({int(frame_at(scene, t)) for t in [*times, *clip_times]})
            for frame in frames:
                scene.frame_set(frame)
                scene.camera = active_camera(scene, frame)
                scene.render.filepath = str(folder / f"frame_{frame:06d}.png")
                bpy.ops.render.render(write_still=True)
            row.update(
                status="success",
                images=[f"{folder.name}/frame_{int(frame_at(scene, t)):06d}.png" for t in times],
                clip_frames=[
                    f"{folder.name}/frame_{int(frame_at(scene, t)):06d}.png" for t in clip_times
                ],
            )
        except Exception as exc:
            row.update(status="error", error=str(exc))
            result["status"] = "partial"
        result["variants"].append(row)
        (output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def save_selected(request):
    preview_root = Path(request["preview_root"])
    result = json.loads((preview_root / "result.json").read_text())
    variant = next((v for v in result["variants"] if v["name"] == request["variant_name"]))
    if variant["status"] != "success":
        raise ValueError("Selected preview did not complete")
    base = json.loads((preview_root / "camera_base.json").read_text())
    scene = bpy.context.scene
    apply(scene, base, variant["parameters"])
    scene.frame_set(base["start"])
    scene.camera = active_camera(scene, base["start"])
    bpy.ops.wm.save_as_mainfile(filepath=request["output_blend"])
    return {
        "status": "success",
        "variant_name": variant["name"],
        "output_blend": request["output_blend"],
        "camera_names": sorted({s["camera"] for s in base["shots"]}),
        "animation": "baked world-space camera transforms; source scene preserved in preview/base.blend",
    }
