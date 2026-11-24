"""Select existing sequence frames by playback time without changing video timing."""

from fractions import Fraction
import math


def sample_clip(total_frames, source_fps, start_seconds, duration_seconds, fps):
    values = {
        "source_fps": source_fps,
        "start_seconds": start_seconds,
        "duration_seconds": duration_seconds,
        "fps": fps,
    }
    for name, value in values.items():
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or (not math.isfinite(value))
        ):
            raise ValueError(f"{name} must be a finite number")
        if value < 0 or (name != "start_seconds" and value == 0):
            raise ValueError(
                f"{name} must be {('nonnegative' if name == 'start_seconds' else 'positive')}"
            )
    if total_frames <= 0:
        raise ValueError("The sequence has no frames")
    (source, start, duration, requested_rate) = (
        Fraction(str(values[name]))
        for name in ("source_fps", "start_seconds", "duration_seconds", "fps")
    )
    video_end = Fraction(total_frames) / source
    if start >= video_end:
        raise ValueError("start_seconds is outside the sequence")
    end = min(start + duration, video_end)
    rate = min(requested_rate, source)
    samples = []
    for offset in range(math.ceil((end - start) * rate)):
        when = start + Fraction(offset) / rate
        index = int(when * source)
        samples.append(
            {
                "frame_index": index,
                "sample_time_seconds": float(when),
                "timestamp_seconds": float(Fraction(index) / source),
            }
        )
    return {
        "source_fps": float(source),
        "requested_preview_fps": float(requested_rate),
        "preview_fps": float(rate),
        "start_seconds": float(start),
        "end_seconds": float(end),
        "requested_duration_seconds": duration_seconds,
        "duration_seconds": float(end - start),
        "video_duration_seconds": float(video_end),
        "clipped": end < start + duration,
        "samples": samples,
    }
