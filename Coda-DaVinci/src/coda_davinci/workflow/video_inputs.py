"""Validate a complete proxy and resolve the full movie's reference bindings."""

import json
from dataclasses import dataclass
from coda_davinci.media.artifacts import file_hash, local_path
from coda_davinci.workflow.references import ReferenceImage, load_shot_manifest, proxy_observations
from coda_davinci.media.reference_videos import ReferenceVideo, video_upload_mapping
from coda_davinci.media.subjects import bind_manifest
from coda_davinci.media.video_encoding import frame_rate
from coda_davinci.media.video_media import sample_times


@dataclass
class VideoInputs:
    prompt: str
    duration: float
    manifest: dict
    sources: list
    tracked_files: list


def prepare_video_inputs(
    tools,
    prompt,
    proxy_video,
    reference_videos,
    reference_images,
    shot_manifest_path,
    duration_seconds=None,
):
    direct = getattr(tools.settings, "generation_route", "blender") == "direct"
    if direct and (proxy_video or shot_manifest_path):
        raise ValueError("Direct generation requires null proxy_video and shot_manifest_path")
    if (
        ("auto" if tools.settings.generation_route == "direct" else "required") == "required"
        and (not direct)
        and (not proxy_video or not shot_manifest_path)
    ):
        raise ValueError(
            "Experiment requires a current proxy_video and shot_manifest_path in every round"
        )
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("Write the full movie's generation prompt using the video-pe skill")
    if not isinstance(reference_videos, list) or not isinstance(reference_images, list):
        raise ValueError("reference_videos and reference_images must be lists; [] is allowed")
    if not proxy_video and (not tools.settings.interactive) and (not direct):
        raise ValueError("The non-interactive full-video workflow requires a proxy")
    proxy = local_path(tools.root, proxy_video) if proxy_video else None
    manifest_path = local_path(tools.root, shot_manifest_path) if shot_manifest_path else None
    (labels, videos, images, sources) = (set(), [], [], [])
    for kind, entries in (("video", reference_videos), ("image", reference_images)):
        for item in entries:
            if not isinstance(item, dict) or set(item) != {"label", "path"}:
                raise ValueError("Each reference requires exactly label and path")
            label = item["label"]
            if (
                not isinstance(label, str)
                or not label.strip()
                or label in labels
                or (label == "白模视频")
            ):
                raise ValueError(
                    "Reference labels must be unique; 白模视频 is reserved for the proxy"
                )
            labels.add(label)
            source = local_path(tools.root, item["path"])
            if kind == "video":
                videos.append(ReferenceVideo(label, source, {}, ()))
                sources.append((kind, label, source))
            else:
                images.append(ReferenceImage(label, source))
    tracked = []
    if proxy is not None:
        if manifest_path is None:
            raise ValueError("A proxy requires its shot_manifest_path")
        manifest = load_shot_manifest(
            manifest_path, expected_render_id=proxy.parent.name, reference_labels=set()
        )
        if manifest.get("appearance_mode") in {"original", "neutral_clay"}:
            raise ValueError("The proxy requires identity-color materials")
        proxy_observations(proxy, manifest)
        sequence_path = proxy.parent / "sequence.json"
        sequence = json.loads(sequence_path.read_text())
        proxy_duration = len(sequence["frames"]) / frame_rate(sequence["fps"])
        duration = duration_seconds if duration_seconds is not None else proxy_duration
        tracked.extend([manifest_path, sequence_path])
        (manifest, images, identity_contract) = bind_manifest(
            tools.subjects, manifest, images, preserve_reference_set=False
        )
    else:
        if not reference_videos and (not reference_images) and (not direct):
            raise ValueError("Reference-only continuation requires at least an image or video")
        duration = (
            duration_seconds
            if duration_seconds is not None
            else tools.settings.video_total_duration
        )
        if duration is None:
            raise ValueError("Reference-only generation needs duration_seconds")
        identity_contract = ""
        manifest = {"entity_map": [], "shots": []}
        if manifest_path:
            manifest = json.loads(manifest_path.read_text())
            (manifest, images, identity_contract) = bind_manifest(
                tools.subjects, manifest, images, preserve_reference_set=False
            )
            tracked.append(manifest_path)
    from coda_davinci.workflow.continuation import check_inputs

    check_inputs(tools, images, videos)
    labels = {v.label for v in videos}
    for image in images:
        if image.label in labels or image.label == "白模视频":
            raise ValueError("Image and video labels must be distinct")
        labels.add(image.label)
        if image.path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            raise ValueError("Reference images must be PNG/JPEG/WebP")
    for entity in manifest["entity_map"]:
        for binding in entity.get("reference_bindings", []):
            if binding["media_label"] not in labels:
                raise ValueError(f"Unknown reference label: {binding['media_label']}")
    if proxy is not None:
        manifest["proxy_video_label"] = "白模视频"
    sources.extend((("image", r.label, r.path) for r in images))
    if proxy is not None:
        sources.append(("proxy", "白模视频", proxy))
    mapping = video_upload_mapping(videos, "白模视频" if proxy else None)
    if proxy is not None and (not videos):
        mapping = "\nVideo 1 / 视频1: 白模视频 (Blender proxy)."
    if images:
        mapping += "\nImage attachment order:\n" + "\n".join(
            (f"Image {i} / 图片{i}: {r.label}" for (i, r) in enumerate(images, 1))
        )
    from coda_davinci.workflow.bindings import color_prompt

    color_control = color_prompt(tools.settings, manifest) if proxy is not None else ""
    if color_control:
        prompt += "\n\n" + color_control
    prompt += ("\n\n" + identity_contract if identity_contract else "") + mapping
    tracked.extend((source for (_, _, source) in sources))
    if tools.subjects.path.is_file():
        tracked.append(tools.subjects.path)
    fingerprints = [
        {"path": p.relative_to(tools.root).as_posix(), "sha256": file_hash(p)}
        for p in dict.fromkeys(tracked)
    ]
    return VideoInputs(prompt, duration, manifest, sources, fingerprints)


def required_review_times(manifest, duration, fps):
    """Review every known shot, including frames on both sides of each cut."""
    required = sample_times(duration, fps)
    source_fps = manifest.get("fps")
    if source_fps and all(
        ("start_frame" in shot and "end_frame" in shot for shot in manifest["shots"])
    ):
        for shot in manifest["shots"]:
            (start, end) = (shot["start_frame"], shot["end_frame"])
            required.extend(
                (index / source_fps for index in (start, (start + end - 1) / 2, end - 1))
            )
    else:
        count = max(1, len(manifest["shots"]))
        required.extend((duration * (i + 0.5) / count for i in range(count)))
    last = max(0.0, duration - 1 / fps)
    return sorted({min(last, max(0.0, t)) for t in required})
