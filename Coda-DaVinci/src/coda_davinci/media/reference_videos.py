"""Reference video snapshots, sparse observations, and provider-neutral labels."""

from bisect import bisect_right
from dataclasses import dataclass
import json
from pathlib import Path
import shutil
from coda_davinci.media.external_video import VideoReader
from coda_davinci.media.image_transport import image_data_url


@dataclass(frozen=True)
class ReferenceVideo:
    label: str
    path: Path
    metadata: dict
    observations: tuple


def prepare_reference_videos(specs, workspace, settings, *, image_labels=(), preview=True):
    labels = set(image_labels)
    videos = []
    if specs and "白模视频" in labels:
        raise ValueError("白模视频 is reserved for the generated proxy, not a reference image")
    for number, spec in enumerate(specs, 1):
        (label, separator, value) = spec.partition("=")
        label = label.strip()
        if not separator or not label or label in labels or (label == "白模视频"):
            raise ValueError(
                "Reference videos require unique LABEL=PATH; 白模视频 is reserved for the proxy"
            )
        source = Path(value).expanduser().resolve()
        if not source.is_file() or source.suffix.lower() not in {
            ".mp4",
            ".mov",
            ".mkv",
            ".webm",
            ".avi",
        }:
            raise ValueError(f"Expected an existing MP4/MOV/MKV/WebM/AVI: {source}")
        labels.add(label)
        target = workspace / "inputs" / f"reference_video_{number:03d}" / source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        (observations, metadata) = ([], {})
        if preview:
            reader = VideoReader(target, workspace, settings)
            metadata = reader.metadata()
            count = min(8, metadata["total_frames"])
            times = reader.info["timestamps"]
            indices = sorted(
                {bisect_right(times, n * times[-1] / max(1, count - 1)) - 1 for n in range(count)}
            )
            frames = reader.frames(indices)
            observations = [
                {
                    "label": f"Reference video {label}; time={reader.info['timestamps'][i]:.6f}s; frame_index={i}",
                    "path": p.relative_to(workspace).as_posix(),
                }
                for (i, p) in frames.items()
            ]
        videos.append(ReferenceVideo(label, target, metadata, tuple(observations)))
    if videos:
        meta = workspace / ".harness"
        meta.mkdir(exist_ok=True)
        (meta / "reference_videos.json").write_text(
            json.dumps(
                [
                    {
                        "label": v.label,
                        "path": v.path.relative_to(workspace).as_posix(),
                        **v.metadata,
                        "observations": v.observations,
                    }
                    for v in videos
                ],
                ensure_ascii=False,
                indent=2,
            )
        )
    return videos


def reference_video_brief(videos, workspace):
    if not videos:
        return ""
    return (
        "\nReference videos (original input, separate from the generated proxy):\n"
        + json.dumps(
            [
                {"label": v.label, "path": v.path.relative_to(workspace).as_posix(), **v.metadata}
                for v in videos
            ],
            ensure_ascii=False,
        )
        + "\nSparse timestamped previews are provided. Infer each reference's purpose from the original request; do not assume all appearance, motion and camera details must be copied. Use video_info and view_video_clip/view_video_frame for closer inspection of important actions. Pass video files explicitly in child task inputs when delegating. Use 白模视频 as the generated shot_manifest.proxy_video_label, distinct from user references. These visual previews do not convey audio.\n"
    )


def video_media(videos):
    return [
        {
            "label": v.label,
            "media": "<original reference video; sparse timestamped observations attached>",
            **v.metadata,
        }
        for v in videos
    ]


def video_observations(videos, workspace):
    from coda_davinci.workflow.references import ImageObservation

    return [
        ImageObservation(o["label"], workspace / o["path"]) for v in videos for o in v.observations
    ]


def initial_video_message(workspace, protocol):
    path = workspace / ".harness/reference_videos.json"
    if not path.is_file():
        return []
    observations = [o for v in json.loads(path.read_text()) for o in v["observations"]]
    content = []
    for item in observations:
        image = (workspace / item["path"]).resolve()
        if not image.is_relative_to(workspace.resolve()):
            raise ValueError("Reference preview must stay inside the workspace")
        url = image_data_url(image)
        if protocol == "responses":
            content.extend(
                [
                    {"type": "input_text", "text": item["label"]},
                    {"type": "input_image", "image_url": url, "detail": "high"},
                ]
            )
        elif protocol == "chat":
            content.extend(
                [
                    {"type": "text", "text": item["label"]},
                    {"type": "image_url", "image_url": {"url": url, "detail": "high"}},
                ]
            )
        else:
            (header, data) = url.split(",", 1)
            content.extend(
                [
                    {"type": "text", "text": item["label"]},
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": header[5:].split(";")[0],
                            "data": data,
                        },
                    },
                ]
            )
    return [{"role": "user", "content": content}] if content else []


def video_upload_mapping(videos, proxy_label=None):
    labels = [v.label for v in videos] + ([proxy_label] if proxy_label else [])
    if not videos:
        return ""
    return "\n\nVideo attachment order (labels refer to these original full videos):\n" + "\n".join(
        (f"视频{i} / video {i}: {label}" for (i, label) in enumerate(labels, 1))
    )
