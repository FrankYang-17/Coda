"""Indexed, cached video observations using FFprobe timestamps and FFmpeg decoding."""

from bisect import bisect_right
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import uuid
from coda_davinci.runtime.sandbox import wrap_command


class VideoReader:
    def __init__(self, path, workspace, settings, run=None):
        self.path = Path(path).resolve()
        self.root = Path(workspace).resolve()
        if not self.path.is_relative_to(self.root) or not self.path.is_file():
            raise ValueError("Video must be an existing file inside the workspace")
        self.settings = settings
        self.run = run or self._run
        stat = self.path.stat()
        identity = f"{self.path}:{stat.st_size}:{stat.st_mtime_ns}:{stat.st_ctime_ns}"
        self.cache = self.root / "video_previews" / hashlib.sha256(identity.encode()).hexdigest()
        if (self.root / "video_previews").is_symlink() or self.cache.is_symlink():
            raise ValueError("Video cache directories must not be symbolic links")
        self.cache.mkdir(parents=True, exist_ok=True)
        self.info = self._probe()

    def _run(self, command):
        if self.settings.sandbox:
            command = wrap_command(
                command, executable=self.settings.bwrap, workspace=self.root, cwd=self.root
            )
        result = subprocess.run(
            command,
            cwd=self.root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=self.settings.tool_timeout,
            check=False,
            env={
                k: v
                for (k, v) in os.environ.items()
                if k
                not in {
                    "API_KEY",
                    "OPENAI_API_KEY",
                    "VIDEO_MODEL_API_KEY",
                    "GPT_API_KEY",
                    "CLAUDE_API_KEY",
                    "IMAGE_API_KEY",
                }
            },
        )
        return {
            "status": "success" if result.returncode == 0 else "error",
            "output": result.stdout.decode(errors="replace"),
            "log_tail": result.stdout.decode(errors="replace")[-4000:],
        }

    def _execute(self, command):
        result = self.run(command)
        if result["status"] != "success":
            raise ValueError(f"Video inspection failed: {result.get('log_tail', result['status'])}")
        return result

    def _probe(self):
        target = self.cache / "index.json"
        if target.is_file():
            return json.loads(target.read_text())
        temporary = self.cache / f"probe_{uuid.uuid4().hex}.json"
        ffprobe = (
            getattr(self.settings, "ffprobe", None)
            or os.environ.get("FFPROBE_EXECUTABLE")
            or str(Path(self.settings.ffmpeg).with_name("ffprobe"))
        )
        try:
            result = self._execute(
                [
                    ffprobe,
                    "-v",
                    "error",
                    "-select_streams",
                    "v:0",
                    "-show_frames",
                    "-show_streams",
                    "-show_entries",
                    "frame=best_effort_timestamp_time,duration_time,pkt_duration_time:stream=width,height,duration,avg_frame_rate",
                    "-of",
                    "json",
                    str(self.path),
                ]
            )
            output = (
                result["output"] if "output" in result else (self.root / result["log"]).read_text()
            )
            raw = json.loads(output)
            (streams, frames) = (raw.get("streams", []), raw.get("frames", []))
            if not streams or not frames:
                raise ValueError("Input has no decodable video frames")
            times = [float(f["best_effort_timestamp_time"]) for f in frames]
            origin = times[0]
            times = [t - origin for t in times]
            if any((not math.isfinite(t) for t in times)) or any(
                (a > b for (a, b) in zip(times, times[1:]))
            ):
                raise ValueError("Video timestamps must be finite and ordered")
            stream = streams[0]
            last_duration = float(
                frames[-1].get("duration_time", frames[-1].get("pkt_duration_time", 0))
            )
            if last_duration <= 0:
                last_duration = (
                    times[-1] - times[-2] if len(times) > 1 else float(stream.get("duration", 0))
                )
            duration = times[-1] + last_duration
            if not math.isfinite(duration) or duration <= times[-1]:
                raise ValueError("Cannot determine video duration")
            info = {
                "width": stream["width"],
                "height": stream["height"],
                "total_frames": len(times),
                "duration_seconds": duration,
                "timestamps": times,
                "source_fps": len(times) / duration,
            }
            temporary.write_text(json.dumps(info))
            temporary.replace(target)
            return info
        finally:
            temporary.unlink(missing_ok=True)

    def metadata(self):
        return {k: v for (k, v) in self.info.items() if k != "timestamps"}

    def frames(self, indices):
        valid = sorted({i for i in indices if 0 <= i < self.info["total_frames"]})
        missing = [i for i in valid if not (self.cache / f"{i}.png").is_file()]
        for offset in range(0, len(missing), 128):
            batch = missing[offset : offset + 128]
            prefix = uuid.uuid4().hex
            pattern = self.cache / f"{prefix}_%06d.png"
            select = "+".join((f"eq(n\\,{i})" for i in batch))
            try:
                self._execute(
                    [
                        self.settings.ffmpeg,
                        "-nostdin",
                        "-v",
                        "error",
                        "-i",
                        str(self.path),
                        "-map",
                        "0:v:0",
                        "-vf",
                        f"select={select}",
                        "-fps_mode",
                        "passthrough",
                        "-frames:v",
                        str(len(batch)),
                        "-threads",
                        "1",
                        str(pattern),
                    ]
                )
                outputs = [self.cache / f"{prefix}_{n:06d}.png" for n in range(1, len(batch) + 1)]
                if not all((p.is_file() for p in outputs)):
                    raise ValueError("Video decoder did not return all requested frames")
                for index, output in zip(batch, outputs):
                    output.replace(self.cache / f"{index}.png")
            finally:
                for output in self.cache.glob(f"{prefix}_*.png"):
                    output.unlink(missing_ok=True)
        return {i: self.cache / f"{i}.png" for i in valid}

    def samples(self, start, duration, fps):
        for name, value in (("start_seconds", start), ("duration_seconds", duration), ("fps", fps)):
            if (
                isinstance(value, bool)
                or not isinstance(value, (float, int))
                or (not math.isfinite(value))
            ):
                raise ValueError(f"{name} must be finite")
            if value < 0 or (name != "start_seconds" and value == 0):
                raise ValueError(f"Invalid {name}")
        end = min(start + duration, self.info["duration_seconds"])
        if start >= end:
            raise ValueError("start_seconds is outside the video")
        times = self.info["timestamps"]
        (samples, previous) = ([], None)
        gaps = [b - a for (a, b) in zip(times, times[1:]) if b > a]
        rate = min(fps, 1 / min(gaps) if gaps else self.info["source_fps"])
        for n in range(math.ceil((end - start) * rate)):
            when = start + n / rate
            if when >= end:
                break
            index = bisect_right(times, when) - 1
            if index != previous:
                samples.append(
                    {
                        "frame_index": index,
                        "timestamp_seconds": times[index],
                        "sample_time_seconds": when,
                    }
                )
                previous = index
        return {
            "samples": samples,
            "start_seconds": start,
            "end_seconds": end,
            "duration_seconds": end - start,
            "requested_duration_seconds": duration,
            "video_duration_seconds": self.info["duration_seconds"],
            "source_fps": self.info["source_fps"],
            "preview_fps": rate,
            "requested_preview_fps": fps,
            "clipped": end < start + duration,
        }
