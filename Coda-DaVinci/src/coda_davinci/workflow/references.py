"""Reference media, multimodal routing requests and proxy manifests."""

from __future__ import annotations
import json
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from coda_davinci.providers.api_retry import get_request_id, with_request_id
from coda_davinci.agents.model_stream import responses_stream, chat_stream
from coda_davinci.media.image_transport import image_data_url

LOG = logging.getLogger(__name__)
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp"})
DEFAULT_PROXY_VIDEO_LABEL = "视频1"
NO_MANIFEST_MAX_OBSERVATIONS = 24


@dataclass(frozen=True)
class ReferenceImage:
    label: str
    path: Path


@dataclass(frozen=True)
class ImageObservation:
    label: str
    path: Path


def prepare_reference_images(specs: list[str], workspace: Path) -> list[ReferenceImage]:
    """Copy LABEL=PATH CLI inputs into the episode so every later stage sees one snapshot."""
    inputs = workspace / "inputs"
    references: list[ReferenceImage] = []
    labels: set[str] = set()
    for index, spec in enumerate(specs, start=1):
        if "=" not in spec:
            raise ValueError("Reference images must use LABEL=PATH, for example 图片1=/path/a.png")
        (label, source_value) = spec.split("=", 1)
        label = label.strip()
        source = Path(source_value).expanduser().resolve()
        if not label:
            raise ValueError("Reference image label must not be empty")
        if label in labels:
            raise ValueError(f"Duplicate reference image label: {label}")
        if not source.is_file() or source.suffix.lower() not in IMAGE_SUFFIXES:
            raise ValueError(f"Reference image must be an existing PNG/JPEG/WebP file: {source}")
        labels.add(label)
        inputs.mkdir(parents=True, exist_ok=True)
        target = inputs / f"reference_{index:03d}{source.suffix.lower()}"
        shutil.copyfile(source, target)
        references.append(ReferenceImage(label, target))
    return references


def user_prompt_value(text: str, references: list[ReferenceImage], reference_videos=()) -> dict:
    from coda_davinci.media.reference_videos import video_media

    return {
        "text": text,
        "reference_media": [
            {"label": reference.label, "media": "<attached reference image>"}
            for reference in references
        ]
        + video_media(reference_videos),
    }


def _data_url(path: Path) -> str:
    return image_data_url(path)


def _response_dump(response) -> dict:
    if hasattr(response, "model_dump"):
        return response.model_dump(mode="json")
    if isinstance(response, dict):
        return response
    return {"repr": repr(response)}


def _openai_responses_call(
    client, settings, system: str, payload: dict, observations: list[ImageObservation]
):
    content = [{"type": "input_text", "text": json.dumps(payload, ensure_ascii=False)}]
    for observation in observations:
        content.extend(
            [
                {"type": "input_text", "text": observation.label},
                {"type": "input_image", "detail": "high", "image_url": _data_url(observation.path)},
            ]
        )
    options = (
        {"reasoning": {"effort": settings.reasoning_effort}}
        if settings.reasoning_effort is not None
        else {}
    )
    response = responses_stream(
        client,
        model=settings.model,
        instructions=system,
        input=[{"role": "user", "content": content}],
        store=False,
        **options,
    )
    if getattr(response, "status", "completed") != "completed":
        raise with_request_id(
            RuntimeError(f"PE response did not complete: {getattr(response, 'status', None)}"),
            response,
        )
    return (response.output_text, response)


def _openai_chat_call(
    client, settings, system: str, payload: dict, observations: list[ImageObservation]
):
    content = [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]
    for observation in observations:
        content.extend(
            [
                {"type": "text", "text": observation.label},
                {
                    "type": "image_url",
                    "image_url": {"url": _data_url(observation.path), "detail": "high"},
                },
            ]
        )
    options = (
        {"reasoning_effort": settings.reasoning_effort}
        if settings.reasoning_effort is not None
        else {}
    )
    response = chat_stream(
        client,
        model=settings.model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": content}],
        **options,
    )
    if len(response.choices) != 1 or response.choices[0].finish_reason != "stop":
        raise with_request_id(RuntimeError("PE chat response did not finish normally"), response)
    text = response.choices[0].message.content
    if not isinstance(text, str):
        raise with_request_id(RuntimeError("PE chat response did not contain text"), response)
    return (text, response)


def _anthropic_call(
    client, settings, system: str, payload: dict, observations: list[ImageObservation]
):
    content = [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]
    for observation in observations:
        (header, data) = _data_url(observation.path).split(",", 1)
        content.extend(
            [
                {"type": "text", "text": observation.label},
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": header[5:].split(";", 1)[0],
                        "data": data,
                    },
                },
            ]
        )
    options = (
        {"output_config": {"effort": settings.reasoning_effort}}
        if settings.reasoning_effort is not None
        else {}
    )
    with client.messages.stream(
        model=settings.model,
        system=system,
        messages=[{"role": "user", "content": content}],
        max_tokens=settings.max_tokens,
        **options,
    ) as stream:
        try:
            response = stream.get_final_message()
            response._request_id = get_request_id(response) or get_request_id(stream)
        except Exception as exc:
            raise with_request_id(exc, stream)
    if response.stop_reason not in {"end_turn", "stop_sequence"}:
        raise with_request_id(
            RuntimeError(f"PE Anthropic response did not finish normally: {response.stop_reason}"),
            response,
        )
    text = "".join((block.text for block in response.content if block.type == "text"))
    return (text, response)


def parse_pe_json(text: str) -> dict:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("PE response is empty")
    value = text.strip()
    if "</think>" in value:
        value = value.rsplit("</think>", 1)[1].strip()
    if value.startswith("```"):
        first_newline = value.find("\n")
        value = value[first_newline + 1 :] if first_newline >= 0 else value[3:]
        if value.rstrip().endswith("```"):
            value = value.rstrip()[:-3].rstrip()
    start = value.find("{")
    if start < 0:
        raise ValueError("PE response does not contain a JSON object")
    try:
        (result, end) = json.JSONDecoder().raw_decode(value[start:])
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid PE JSON: {exc}") from exc
    if value[start + end :].strip():
        raise ValueError("PE response contains text after the JSON object")
    if not isinstance(result, dict):
        raise ValueError("PE response must be a JSON object")
    return result


def resolve_shot_manifest_path(workspace: Path, value: str) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (workspace / path).resolve()


def load_shot_manifest(path: Path, *, expected_render_id: str, reference_labels: set[str]) -> dict:
    if not path.is_file():
        raise FileNotFoundError(f"shot_manifest was not provided: {path}")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"shot_manifest is not valid JSON: {exc}") from exc
    if not isinstance(manifest, dict):
        raise ValueError("shot_manifest must be a JSON object")
    if manifest.get("render_id") != expected_render_id:
        raise ValueError(
            f"shot_manifest.render_id must match the selected proxy render_uuid ({expected_render_id})"
        )
    if not isinstance(manifest.get("proxy_video_label"), str) or not manifest["proxy_video_label"]:
        raise ValueError("shot_manifest.proxy_video_label must be a nonempty string")
    entities = manifest.get("entity_map")
    shots = manifest.get("shots")
    if not isinstance(entities, list) or not isinstance(shots, list) or (not shots):
        raise ValueError("shot_manifest requires entity_map and at least one shot")
    entity_ids: set[str] = set()
    color_names: set[str] = set()
    color_hexes: set[str] = set()
    for entity in entities:
        if not isinstance(entity, dict):
            raise ValueError("Each entity_map entry must be an object")
        entity_id = entity.get("entity_id")
        subject = entity.get("user_subject")
        color = entity.get("proxy_color")
        if not isinstance(entity_id, str) or not entity_id or entity_id in entity_ids:
            raise ValueError("entity_map entity_id values must be nonempty and unique")
        if not isinstance(subject, str) or not subject:
            raise ValueError(f"entity_map {entity_id} requires user_subject")
        if manifest.get("appearance_mode") not in {"original", "neutral_clay"}:
            if not isinstance(color, dict) or not isinstance(color.get("name"), str):
                raise ValueError(f"entity_map {entity_id} requires proxy_color.name")
            color_name = color["name"].strip().casefold()
            color_hex = str(color.get("hex", "")).strip().casefold()
            if color_name in color_names or (color_hex and color_hex in color_hexes):
                raise ValueError("Tracked entities must not reuse the same proxy color")
            color_names.add(color_name)
            if color_hex:
                color_hexes.add(color_hex)
        entity_ids.add(entity_id)
        bindings = entity.get("reference_bindings", [])
        if not isinstance(bindings, list):
            raise ValueError(f"entity_map {entity_id} reference_bindings must be an array")
        for binding in bindings:
            label = binding.get("media_label") if isinstance(binding, dict) else None
            if reference_labels and label not in reference_labels:
                raise ValueError(f"entity_map {entity_id} references unknown media label: {label}")
    shot_ids: set[str] = set()
    intervals = []
    for shot in shots:
        if not isinstance(shot, dict):
            raise ValueError("Each shot entry must be an object")
        shot_id = shot.get("shot_id")
        visible = shot.get("visible_entity_ids")
        if not isinstance(shot_id, str) or not shot_id or shot_id in shot_ids:
            raise ValueError("shot_id values must be nonempty and unique")
        if not isinstance(visible, list) or not all((item in entity_ids for item in visible)):
            raise ValueError(f"Shot {shot_id} contains an unknown visible_entity_id")
        if not isinstance(shot.get("description"), str) or not shot["description"].strip():
            raise ValueError(f"Shot {shot_id} requires a nonempty description")
        shot_ids.add(shot_id)
        (start, end) = (shot.get("start_frame"), shot.get("end_frame"))
        if (start is None) != (end is None):
            raise ValueError(f"Shot {shot_id} must provide both start_frame and end_frame")
        if start is not None:
            if any(
                (isinstance(value, bool) or not isinstance(value, int) for value in (start, end))
            ):
                raise ValueError(f"Shot {shot_id} frame bounds must be integers")
            if start < 0 or end <= start:
                raise ValueError(
                    f"Shot {shot_id} must use a nonempty [start_frame, end_frame) interval"
                )
            intervals.append((shot_id, start, end))
    if intervals and len(intervals) != len(shots):
        raise ValueError("Either every shot or no shot must provide frame intervals")
    if intervals:
        if manifest.get("frame_interval_convention") != "[start_frame, end_frame)":
            raise ValueError(
                "Frame-based shot_manifest must declare [start_frame, end_frame) convention"
            )
        total = manifest.get("total_frames")
        fps = manifest.get("fps")
        if isinstance(total, bool) or not isinstance(total, int) or total <= 0:
            raise ValueError("Frame-based shot_manifest requires positive total_frames")
        if isinstance(fps, bool) or not isinstance(fps, (int, float)) or fps <= 0:
            raise ValueError("Frame-based shot_manifest requires positive fps")
        if intervals[0][1] != 0 or intervals[-1][2] != total:
            raise ValueError(
                "Shot frame intervals must cover the complete proxy from 0 to total_frames"
            )
        for previous, current in zip(intervals, intervals[1:]):
            if previous[2] != current[1]:
                raise ValueError("Shot frame intervals must be chronological and contiguous")
    preserve = manifest.get("preserve_attributes", [])
    if not isinstance(preserve, list):
        raise ValueError("shot_manifest.preserve_attributes must be an array")
    return manifest


def _load_proxy_sequence(proxy_video: Path) -> tuple[dict, list[Path]]:
    if not proxy_video.is_file():
        raise FileNotFoundError(f"Proxy video is missing: {proxy_video}")
    sequence_path = proxy_video.parent / "sequence.json"
    if not sequence_path.is_file():
        raise FileNotFoundError(f"Proxy sequence manifest is missing: {sequence_path}")
    sequence = json.loads(sequence_path.read_text(encoding="utf-8"))
    frames = sequence.get("frames")
    if not isinstance(frames, list) or not frames:
        raise ValueError("Proxy sequence.json requires a nonempty frames array")
    root = proxy_video.parent.resolve()
    frame_paths = []
    for frame in frames:
        if not isinstance(frame, str) or not frame:
            raise ValueError("Proxy sequence.json frame entries must be nonempty strings")
        path = (proxy_video.parent / frame).resolve()
        if not path.is_file() or not path.is_relative_to(root):
            raise ValueError(f"Proxy source frame is missing or invalid: {frame}")
        frame_paths.append(path)
    return (sequence, frame_paths)


def proxy_observations(proxy_video: Path, manifest: dict) -> list[ImageObservation]:
    """Select ordered source frames that cover every manifest shot."""
    (sequence, frame_paths) = _load_proxy_sequence(proxy_video)
    frames = sequence["frames"]
    total_frames = manifest.get("total_frames")
    if total_frames is not None and total_frames != len(frames):
        raise ValueError(
            f"shot_manifest total_frames ({total_frames}) does not match rendered sequence length ({len(frames)})"
        )
    manifest_fps = manifest.get("fps")
    sequence_fps = sequence.get("fps")
    if (
        manifest_fps is not None
        and sequence_fps is not None
        and (abs(float(manifest_fps) - float(sequence_fps)) > 1e-06)
    ):
        raise ValueError(
            f"shot_manifest fps ({manifest_fps}) does not match sequence.json fps ({sequence_fps})"
        )
    shots = manifest["shots"]
    selected: list[tuple[str, int]] = []
    if all(("start_frame" in shot for shot in shots)):
        for shot in shots:
            (start, end) = (shot["start_frame"], shot["end_frame"])
            indices = (start, (start + end - 1) // 2, end - 1)
            for index in dict.fromkeys(indices):
                selected.append((shot["shot_id"], index))
    else:
        for index, shot in enumerate(shots):
            frame_index = min(len(frames) - 1, (2 * index + 1) * len(frames) // (2 * len(shots)))
            selected.append((shot["shot_id"], frame_index))
    observations = []
    seen: set[tuple[str, int]] = set()
    for shot_id, index in selected:
        key = (shot_id, index)
        if key in seen:
            continue
        if index >= len(frames):
            raise ValueError(
                f"shot_manifest frame {index} exceeds rendered sequence length {len(frames)}"
            )
        seen.add(key)
        observations.append(
            ImageObservation(
                f"{manifest['proxy_video_label']} source observation: {shot_id}, frame_index={index}",
                frame_paths[index],
            )
        )
    return observations
