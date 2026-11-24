"""Resolve ordered PNG sequences without starting Blender or FFmpeg."""

import json
import math
from pathlib import Path
import re
import struct
from coda_davinci.media.artifacts import local_path


def frame_rate(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("fps must be a positive finite number")
    if not math.isfinite(value) or value <= 0:
        raise ValueError("fps must be a positive finite number")
    return float(value)


def _frame_key(path):
    parts = re.split("(-?\\d+)", path.name)
    return (
        tuple(((1, int(part)) if re.fullmatch("-?\\d+", part) else (0, part) for part in parts)),
        path.name,
    )


def _png_size(path):
    with path.open("rb") as stream:
        header = stream.read(24)
    if len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        raise ValueError(f"Invalid PNG header: {path}")
    size = struct.unpack(">II", header[16:24])
    if min(size) <= 0:
        raise ValueError(f"Invalid PNG dimensions: {path}")
    return size


def collect_frames(root: Path, frame_dirs: list[str], fps=None):
    """Keep directory order and each manifest's explicit order; never sort across segments."""
    if not isinstance(frame_dirs, list) or not frame_dirs:
        raise ValueError("frame_dirs must be a nonempty list of workspace frame directories")
    (frames, segments, rates) = ([], [], [])
    size = None
    for value in frame_dirs:
        directory = local_path(root, value)
        if not directory.is_dir():
            raise ValueError(f"Not a frame directory: {value}")
        manifest_path = directory.parent / "sequence.json"
        rate = None
        if manifest_path.is_file():
            manifest_path = local_path(root, manifest_path.relative_to(root).as_posix())
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            names = manifest.get("frames") if isinstance(manifest, dict) else None
            if not isinstance(names, list) or not names:
                raise ValueError(f"Missing frame list in {manifest_path.relative_to(root)}")
            selected = [local_path(manifest_path.parent, name) for name in names]
            rate = frame_rate(manifest["fps"]) if manifest.get("fps") is not None else None
        else:
            selected = sorted(
                (p for p in directory.iterdir() if p.suffix.lower() == ".png"), key=_frame_key
            )
        if not selected:
            raise ValueError(f"No PNG frames in {value}")
        for path in selected:
            path = path.resolve()
            if not path.is_relative_to(directory) or not path.is_relative_to(root):
                raise ValueError(f"Frame escapes its directory: {value}")
            if path.suffix.lower() != ".png" or not path.is_file():
                raise ValueError(f"Missing or non-PNG frame: {path}")
            current_size = _png_size(path)
            if size is not None and current_size != size:
                raise ValueError("All input frames must have the same dimensions")
            size = current_size
            frames.append(path)
        rates.append(rate)
        segments.append({"directory": value, "frame_count": len(selected), "source_fps": rate})
    if fps is None:
        if any((rate is None for rate in rates)):
            raise ValueError(
                "Supply fps when a frame directory has no sequence.json or no recorded fps"
            )
        fps = rates[0]
        if any((not math.isclose(rate, fps, rel_tol=1e-09) for rate in rates)):
            raise ValueError(
                "Input frame rates differ; supply fps explicitly to choose playback speed"
            )
    return (frames, frame_rate(fps), segments)
