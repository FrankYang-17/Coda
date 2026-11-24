"""Decode real video files for technical validation and model-visible inspection."""

import json
from bisect import bisect_right
import logging
import math
import re
import threading
import uuid
from coda_davinci.media.artifacts import file_hash
from coda_davinci.tools.tool_results import ToolResult, ViewedImage

LOG = logging.getLogger(__name__)


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def sample_times(duration, fps):
    return sorted(set([0.0, duration / 2, max(0.0, duration - 1 / fps)]))


class VideoMedia:
    def __init__(self, tools):
        self.tools = tools
        self.lock = threading.RLock()
        self.preview_sources = {}
        self.delivered = {}

    def run(self, command, directory):
        result = self.tools._run(command, workspace=directory)
        if result["status"] != "success":
            raise ValueError(
                f"Video media command failed; inspect {result['log']}: {result['log_tail']}"
            )
        return self.tools.path(result["log"]).read_text(encoding="utf-8")

    def probe(self, path):
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError("Video must be an existing nonempty file")
        data = json.loads(
            self.run(
                [
                    self.tools.settings.ffprobe,
                    "-v",
                    "error",
                    "-show_entries",
                    "stream=codec_type,width,height,avg_frame_rate,r_frame_rate,duration:format=duration",
                    "-of",
                    "json",
                    str(path),
                ],
                path.parent,
            )
        )
        video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
        if video is None:
            raise ValueError("No video stream")
        value = video.get("duration")
        duration = float(
            value if value not in (None, "N/A", "") else data.get("format", {}).get("duration") or 0
        )
        rate = video.get("avg_frame_rate")
        if rate in (None, "N/A", "0/0", "0/1"):
            rate = video.get("r_frame_rate", "0/1")
        (numerator, denominator) = rate.split("/")
        fps = float(numerator) / float(denominator)
        (width, height) = (int(video["width"]), int(video["height"]))
        if not all((math.isfinite(v) and v > 0 for v in (duration, fps, width, height))):
            raise ValueError("Video has invalid duration, dimensions or FPS")
        return {
            "duration_seconds": duration,
            "fps": fps,
            "width": width,
            "height": height,
            "has_audio": any((s.get("codec_type") == "audio" for s in data["streams"])),
        }

    def check(self, path, *, expected_duration=None, settings=None, minimum=None, maximum=None):
        LOG.info(
            "Video technical check starting: %s | expected_duration=%s", path, expected_duration
        )
        report = {"status": "failed", "issues": [], "warnings": []}
        deviations = (
            report["warnings"]
            if self.tools.settings.review_policy == "practical"
            else report["issues"]
        )
        try:
            report.update(self.probe(path))
            self.run(
                [
                    self.tools.settings.ffmpeg,
                    "-nostdin",
                    "-v",
                    "error",
                    "-xerror",
                    "-i",
                    str(path),
                    "-map",
                    "0:v:0",
                    "-map",
                    "0:a?",
                    "-f",
                    "null",
                    "-",
                ],
                path.parent,
            )
            report["decodable"] = True
            duration = report["duration_seconds"]
            h3 = settings is not None and settings.model in {"minimax-h3", "minimax-h3-max"}
            if not h3:
                if minimum is not None and duration < minimum - 0.001:
                    deviations.append(f"Video duration {duration:g}s is below {minimum:g}s")
                if maximum is not None and duration > maximum + 0.001:
                    deviations.append(f"Video duration {duration:g}s exceeds {maximum:g}s")
                tolerance = max(0.12, 2 / report["fps"])
                if expected_duration is not None and abs(duration - expected_duration) > tolerance:
                    deviations.append(
                        f"Expected {expected_duration:g}s, received {duration:g}s (tolerance {tolerance:g}s)"
                    )
                if settings is not None:
                    match = re.fullmatch("(\\d+)p", settings.resolution)
                    if match is None:
                        raise ValueError("Video resolution must be a short-edge value such as 720p")
                    edge = int(match[1])
                    if abs(min(report["width"], report["height"]) - edge) > 2:
                        deviations.append(
                            f"Expected {settings.resolution}, received {report['width']}x{report['height']}"
                        )
                    (a, b) = map(float, settings.aspect_ratio.split(":"))
                    if a <= 0 or b <= 0 or (not math.isfinite(a / b)):
                        raise ValueError("Invalid video aspect ratio")
                    if abs(report["width"] - report["height"] * a / b) > 2:
                        deviations.append(f"Expected aspect ratio {settings.aspect_ratio}")
                    if settings.generate_audio and (not report["has_audio"]):
                        deviations.append("Requested audio is missing")
        except (ValueError, OSError, KeyError, ZeroDivisionError) as exc:
            report["issues"].append(str(exc))
        report["status"] = "passed" if not report["issues"] else "failed"
        LOG.info(
            "Video technical check finished: %s | status=%s | duration=%s | resolution=%sx%s | issues=%s",
            path,
            report["status"],
            report.get("duration_seconds"),
            report.get("width"),
            report.get("height"),
            len(report["issues"]),
        )
        return report

    def inspect(self, path, timestamps_seconds):
        source = self.tools.path(path)
        info = self.probe(source)
        digest = file_hash(source)
        asset = self.tools.video_workflow.asset(path) if self.tools.video_workflow else None
        requested = timestamps_seconds or (
            asset["required_times"]
            if asset
            else sample_times(info["duration_seconds"], info["fps"])
        )
        if (
            not isinstance(requested, list)
            or not requested
            or any(
                (
                    isinstance(t, bool)
                    or not isinstance(t, (int, float))
                    or (not math.isfinite(t))
                    or (t < 0)
                    or (t >= info["duration_seconds"])
                    for t in requested
                )
            )
        ):
            raise ValueError(
                "timestamps_seconds must fall within the video; [] chooses review samples"
            )
        directory = self.tools.root / "video_previews" / uuid.uuid4().hex
        directory.mkdir(parents=True)
        from coda_davinci.media.external_video import VideoReader

        reader = VideoReader(source, self.tools.root, self.tools.settings, self.tools._run)
        timestamps = reader.info["timestamps"]
        selected = [
            (timestamp, max(0, bisect_right(timestamps, timestamp) - 1))
            for timestamp in sorted(set(requested))
        ]
        decoded = reader.frames([index for (_, index) in selected])
        (frames, images) = ([], [])
        for timestamp, index in selected:
            target = decoded[index]
            relative = target.relative_to(self.tools.root).as_posix()
            frames.append(
                {
                    "sample_time_seconds": timestamp,
                    "timestamp_seconds": timestamps[index],
                    "frame_index": index,
                    "image": relative,
                }
            )
        comparison = asset.get("comparison") if asset else None
        if comparison and comparison.get("status") == "ready":
            from coda_davinci.media.video_comparison import comparison_frames

            comparison_frames(self.tools, comparison, frames, directory)
        for row in frames:
            target = self.tools.path(row["image"])
            caption = "TOP: proxy; BOTTOM: generated. " if row.get("layout") else ""
            images.append(
                ViewedImage(
                    target,
                    caption
                    + f"Decoded video: {path}; time={row['timestamp_seconds']:.6f}s; sample_time={row['sample_time_seconds']:.6f}s",
                )
            )
            with self.lock:
                self.preview_sources.setdefault(str(target), set()).add(
                    (path, digest, row["sample_time_seconds"])
                )
        if file_hash(source) != digest:
            raise ValueError("Video changed while being inspected; inspect the new version")
        result = {
            "status": "success",
            "source": "decoded_video",
            "video": path,
            "sha256": digest,
            "metadata": info,
            "frames": frames,
            "comparison": comparison,
        }
        save(directory / "inspection.json", result)
        return ToolResult(result, images=images)

    def note_delivered(self, images):
        """An extraction is evidence only after its actual pixels reach the model."""
        with self.lock:
            for image in images:
                for path, digest, timestamp in self.preview_sources.get(str(image.path), ()):
                    self.delivered.setdefault((path, digest), set()).add(timestamp)
                    if self.tools.context:
                        self.tools.context.note_view(self.tools.root / path, self.tools.root)

    def require_any_view(self, path, digest):
        with self.lock:
            if not self.delivered.get((path, digest)):
                raise ValueError("Inspect and collect at least a useful preview before delivery")

    def require_views(self, path, digest, required, fps):
        with self.lock:
            seen = self.delivered.get((path, digest), set()).copy()
        missing = [
            t for t in required if not any((abs(t - s) <= max(0.0001, 0.25 / fps) for s in seen))
        ]
        if missing:
            raise ValueError(
                f"Inspect and collect actual video frames before accepting; missing times: {missing}"
            )
