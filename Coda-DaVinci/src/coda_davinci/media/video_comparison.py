import html
from pathlib import Path
from PIL import Image, ImageDraw, ImageOps


def create_comparison(tools, proxy, generated, directory, generated_info):
    directory.mkdir(parents=True, exist_ok=True)
    proxy_info = tools.video_media.probe(proxy)
    durations = [proxy_info["duration_seconds"], generated_info["duration_seconds"]]
    duration = max(durations)
    branches = []
    for index, (label, length) in enumerate(zip(("PROXY", "GENERATED"), durations)):
        branches.append(
            f"[{index}:v]setpts=PTS-STARTPTS,fps=24,scale=640:360:force_original_aspect_ratio=decrease,pad=640:360:(ow-iw)/2:(oh-ih)/2,setsar=1,tpad=stop_mode=add:stop_duration={duration},trim=duration={duration},drawtext=text='{label}':x=8:y=8:fontsize=18:fontcolor=white:box=1:boxcolor=black@0.7,drawtext=text='ENDED':enable='gte(t,{length})':x=(w-tw)/2:y=(h-th)/2:fontsize=24:fontcolor=white[v{index}]"
        )
    filters = ";".join(branches) + ";[v0][v1]vstack=inputs=2[out]"
    video = directory / "comparison.mp4"
    tools.video_media.run(
        [
            tools.settings.ffmpeg,
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-i",
            str(proxy),
            "-i",
            str(generated),
            "-filter_complex_threads",
            "1",
            "-filter_complex",
            filters,
            "-map",
            "[out]",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "23",
            "-threads",
            "2",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(video),
        ],
        directory,
    )
    page = directory / "index.html"
    template = (Path(__file__).parents[1] / "resources/video_comparison.html").read_text(
        encoding="utf-8"
    )
    page.write_text(
        template.replace(
            "__DURATIONS__", html.escape(f"白模 {durations[0]:.3f} 秒 · 成片 {durations[1]:.3f} 秒")
        ),
        encoding="utf-8",
    )
    return {
        "status": "ready",
        "proxy_video": proxy.relative_to(tools.root).as_posix(),
        "generated_video": generated.relative_to(tools.root).as_posix(),
        "video": video.relative_to(tools.root).as_posix(),
        "page": page.relative_to(tools.root).as_posix(),
        "proxy_duration_seconds": durations[0],
        "generated_duration_seconds": durations[1],
        "timeline": "seconds_from_start",
        "layout": "proxy_above_generated",
        "preview_fps": 24,
        "shorter_video": "black_after_end",
    }


def paired_frame(proxy_frame, generated_frame, output, proxy_time, generated_time):
    canvas = Image.new("RGB", (640, 768), "black")
    draw = ImageDraw.Draw(canvas)
    for y, label, path, timestamp in (
        (0, "PROXY", proxy_frame, proxy_time),
        (384, "GENERATED", generated_frame, generated_time),
    ):
        caption = f"{label} | {timestamp:.3f}s" if path else f"{label} | ENDED"
        draw.text((8, y + 5), caption, fill="white")
        if path:
            with Image.open(path) as image:
                pane = ImageOps.contain(image.convert("RGB"), (640, 360))
                canvas.paste(pane, ((640 - pane.width) // 2, y + 24 + (360 - pane.height) // 2))
    canvas.save(output)


def comparison_frames(tools, comparison, frames, directory):
    from bisect import bisect_right
    from coda_davinci.media.external_video import VideoReader

    reader = VideoReader(
        tools.path(comparison["proxy_video"]), tools.root, tools.settings, tools._run
    )
    indices = [
        max(0, bisect_right(reader.info["timestamps"], row["timestamp_seconds"]) - 1)
        if row["timestamp_seconds"] < comparison["proxy_duration_seconds"]
        else None
        for row in frames
    ]
    decoded = (
        reader.frames([index for index in indices if index is not None])
        if any((i is not None for i in indices))
        else {}
    )
    for number, (row, index) in enumerate(zip(frames, indices)):
        target = directory / f"comparison_{number:03d}.jpg"
        proxy_time = reader.info["timestamps"][index] if index is not None else None
        paired_frame(
            decoded[index] if index is not None else None,
            tools.path(row["image"]),
            target,
            proxy_time,
            row["timestamp_seconds"],
        )
        row.update(
            generated_image=row["image"],
            image=target.relative_to(tools.root).as_posix(),
            proxy_timestamp_seconds=proxy_time,
            layout="proxy_above_generated",
        )
