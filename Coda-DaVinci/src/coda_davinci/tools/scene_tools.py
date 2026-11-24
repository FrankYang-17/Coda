import html
import json
import math
import os
from pathlib import Path
import re
import uuid
from PIL import Image, ImageDraw, ImageOps
from coda_davinci.blender.blender_gpu import bootstrap_expression
from coda_davinci.tools.tool_results import ToolResult, ViewedImage


def execute(tools, blend_file, request, output=None):
    blend = tools.path(blend_file)
    if not blend.is_file() or blend.suffix != ".blend":
        raise ValueError("Expected an existing .blend checkpoint")
    output = output or tools.root / "scene_previews" / uuid.uuid4().hex
    output.mkdir(parents=True, exist_ok=True)
    source = output / "request.json"
    source.write_text(json.dumps(request, ensure_ascii=False, indent=2))
    worker = (Path(__file__).parents[1] / "blender/scene_tool_worker.py").resolve()
    command = tools._blender_base_command() + [
        str(blend),
        "--threads",
        "2",
        "--python-expr",
        bootstrap_expression(),
        "--python",
        str(worker),
        "--",
        str(source),
    ]
    process = tools._run(command, blender=True)
    if process["status"] != "success":
        raise RuntimeError(f"Scene tool failed; inspect {process['log']}: {process['log_tail']}")
    return (output, json.loads((output / "result.json").read_text()))


def inspect_scene_at(tools, blend_file, time_seconds, x, y):
    if (
        not all((math.isfinite(v) for v in (time_seconds, x, y)))
        or time_seconds < 0
        or (not (0 <= x <= 1 and 0 <= y <= 1))
    ):
        raise ValueError("Use a nonnegative time and normalized x/y in [0,1]")
    (output, result) = execute(
        tools, blend_file, {"mode": "inspect", "time_seconds": time_seconds, "x": x, "y": y}
    )
    return {
        "status": "success",
        "blend_file": blend_file,
        "report": (output / "result.json").relative_to(tools.root).as_posix(),
        **result,
    }


def contact_sheet(folder, row, times):
    columns = min(4, len(times))
    canvas = Image.new("RGB", (columns * 320, math.ceil(len(times) / columns) * 204), "#151b25")
    draw = ImageDraw.Draw(canvas)
    for index, (path, timestamp) in enumerate(zip(row["images"], times)):
        (x, y) = (index % columns * 320, index // columns * 204)
        with Image.open(folder / path) as image:
            pane = ImageOps.contain(image.convert("RGB"), (320, 180))
            canvas.paste(pane, (x + (320 - pane.width) // 2, y + (180 - pane.height) // 2))
        draw.text((x + 6, y + 184), f"{timestamp:.3f}s", fill="white")
    target = folder / row["directory"] / "contact.jpg"
    canvas.save(target)
    return target


def preview_camera_variants(
    tools, blend_file, variants, timestamps_seconds, clip_start_seconds, clip_duration_seconds
):
    if not 1 <= len(variants) <= 3 or not 1 <= len(timestamps_seconds) <= 8:
        raise ValueError("Supply 1–3 variants and 1–8 sample times")
    names = [v["name"] for v in variants]
    if any((not n.strip() or n == "baseline" for n in names)) or len(set(names)) != len(names):
        raise ValueError("Variant names must be distinct; baseline is reserved")
    for variant in variants:
        if variant["pace"] not in {"original", "steady", "short_ease"}:
            raise ValueError("Unknown pacing mode")
        if (
            not math.isfinite(variant["distance_scale"])
            or variant["distance_scale"] <= 0
            or (not math.isfinite(variant["height_offset"]))
        ):
            raise ValueError("Use finite camera values and positive distance_scale")
        if variant["lens_mm"] is not None and (
            not math.isfinite(variant["lens_mm"]) or variant["lens_mm"] <= 0
        ):
            raise ValueError("lens_mm must be positive or null")
    if not all((math.isfinite(t) and t >= 0 for t in [*timestamps_seconds, clip_start_seconds])):
        raise ValueError("Preview times must be finite and nonnegative")
    if not math.isfinite(clip_duration_seconds) or not 0 < clip_duration_seconds <= 4:
        raise ValueError("Preview duration must be in (0,4] seconds")
    times = sorted(set(timestamps_seconds))
    (output, result) = execute(
        tools,
        blend_file,
        {
            "mode": "preview",
            "variants": variants,
            "timestamps_seconds": times,
            "clip_start_seconds": clip_start_seconds,
            "clip_duration_seconds": clip_duration_seconds,
        },
    )
    (images, cards) = ([], [])
    for row in result["variants"]:
        label = html.escape(row["name"])
        if row["status"] != "success":
            cards.append(f"<article><h2>{label}</h2><p>{html.escape(row['error'])}</p></article>")
            continue
        folder = output / row["directory"]
        frames = folder / "frames"
        frames.mkdir()
        for index, source in enumerate(row["clip_frames"], 1):
            os.link(output / source, frames / f"frame_{index:08d}.png")
        manifest = {
            "fps": result["preview_fps"],
            "frames": [f"frames/frame_{i:08d}.png" for i in range(1, len(row["clip_frames"]) + 1)],
        }
        (folder / "sequence.json").write_text(json.dumps(manifest))
        encoded = tools._encode_sequence(folder, manifest)
        if encoded["status"] == "success":
            row["video"] = (folder / "video.mp4").relative_to(tools.root).as_posix()
        else:
            row["video_error"] = encoded["log_tail"]
            result["status"] = "partial"
        sheet = contact_sheet(output, row, result["sample_times_seconds"])
        row["contact_sheet"] = sheet.relative_to(tools.root).as_posix()
        images.append(
            ViewedImage(
                sheet, f"Camera variant: {row['name']}; times={result['sample_times_seconds']}"
            )
        )
        video_html = (
            f'''<video controls muted playsinline preload="metadata" src="{row["directory"]}/video.mp4"></video>'''
            if row.get("video")
            else "<p>预演视频编码失败，参见抽帧。</p>"
        )
        cards.append(
            f'''<article><h2>{label}</h2>{video_html}<img src="{row["directory"]}/contact.jpg" alt="镜头采样"><pre>{html.escape(json.dumps(row["parameters"], ensure_ascii=False, indent=2))}</pre></article>'''
        )
        for key in ("images", "clip_frames"):
            row[key] = [(output / path).relative_to(tools.root).as_posix() for path in row[key]]
    template = (Path(__file__).parents[1] / "resources/camera_preview.html").read_text()
    (output / "index.html").write_text(
        template.replace("__CARDS__", "\n".join(cards)).replace(
            "__START__", f"{clip_start_seconds:g}"
        )
    )
    result.update(
        preview_id=output.name,
        page=(output / "index.html").relative_to(tools.root).as_posix(),
        base_blend=(output / "base.blend").relative_to(tools.root).as_posix(),
        next_step="Choose a successful variant and call apply_camera_variant to save a new checkpoint.",
    )
    (output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    return ToolResult(result, images=images)


def apply_camera_variant(tools, preview_id, variant_name, output_blend):
    if not re.fullmatch("[0-9a-f]{32}", preview_id):
        raise ValueError("Invalid preview_id")
    preview = tools.path("scene_previews/" + preview_id)
    available = json.loads((preview / "result.json").read_text())["variants"]
    if not any((v["name"] == variant_name and v["status"] == "success" for v in available)):
        raise ValueError("Choose a successfully rendered variant from this preview")
    target = tools.path(output_blend)
    if (
        target.suffix != ".blend"
        or target.exists()
        or any((target.is_relative_to(p) for p in tools.read_only))
    ):
        raise ValueError("output_blend must be a new writable .blend path")
    target.parent.mkdir(parents=True, exist_ok=True)
    (_, result) = execute(
        tools,
        (preview / "base.blend").relative_to(tools.root).as_posix(),
        {
            "mode": "apply",
            "preview_root": str(preview),
            "variant_name": variant_name,
            "output_blend": str(target),
        },
    )
    result["output_blend"] = target.relative_to(tools.root).as_posix()
    if tools.context:
        with tools.context.team.lock:
            tools.context.assembly_revision += 1
    return result
