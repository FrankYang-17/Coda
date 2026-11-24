"""Continuation frames and reference validation."""

import json
from coda_davinci.media.artifacts import file_hash, local_path
from coda_davinci.media.video_media import save


def tail_frame(workspace, settings, previous_video, number):
    from coda_davinci.media.external_video import VideoReader

    source = local_path(workspace, previous_video)
    reader = VideoReader(source, workspace, settings)
    index = reader.info["total_frames"] - 1
    frame = reader.frames([index])[index]
    record = {
        "round": number,
        "path": frame.relative_to(workspace).as_posix(),
        "sha256": file_hash(frame),
        "source_video": previous_video,
        "source_sha256": file_hash(source),
        "frame_index": index,
    }
    save(workspace / ".harness/continuation.json", record)
    return record


def check_inputs(tools, images, videos):
    """Check after automatic identity resolution, not only explicit tool arguments."""
    image_digests = [file_hash(r.path) for r in images]
    image_hashes = set(image_digests)
    number = getattr(tools.context, "interaction_round", 0)
    tail = None
    if number > 1:
        tail = json.loads((tools.root / ".harness/continuation.json").read_text())
        if (
            tail["round"] != number
            or file_hash(local_path(tools.root, tail["path"])) != tail["sha256"]
        ):
            raise ValueError("Controller tail frame is missing, stale or modified")
        if videos or tail["sha256"] not in image_hashes:
            raise ValueError(
                "This continuation requires the actual previous tail frame and no reference videos"
            )
