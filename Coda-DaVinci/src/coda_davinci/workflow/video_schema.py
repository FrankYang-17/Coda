"""Root-owned full-video generation and visual review tools."""

from coda_davinci.tools.schema import tool, PATH, STRING

REFERENCE_LIST = {
    "type": "array",
    "items": {
        "type": "object",
        "additionalProperties": False,
        "required": ["label", "path"],
        "properties": {"label": STRING, "path": PATH},
    },
}
VIDEO_INSPECTION_TOOLS = [
    tool(
        "inspect_video",
        "Decode actual MP4 pixels, including generated videos. Collect the result with wait_tool. For a generated result with a proxy comparison, returns vertically paired frames: proxy above, generated below, at the same elapsed time. [] selects required samples across all known shots; inspect key actions and uncertain intervals more densely. This inspects pixels, not the original Blender source frames.",
        path=PATH,
        timestamps_seconds={"type": "array", "items": {"type": "number", "minimum": 0}},
    )
]
VIDEO_TOOLS = [
    tool(
        "video_model",
        "Generate one COMPLETE final video from your PE prompt, the inspected full Blender proxy and optional original reference videos/images. Read video-pe first. Duration comes from the proxy; do not split or assemble segments. Returns a generated-video render_uuid, actual MP4 and technical checks. Success is not visual acceptance: inspect_video, collect its pixels and record_video_review before finish. The same revision retrieves/resumes the same immutable attempt; use a higher revision after content changes or a known generation failure. Do not create a new revision merely to poll an uncertain submission.",
        prompt={
            "type": "string",
            "description": "Self-contained generation guidance for the whole video, authored with video-pe. When using minimax-h3 or minimax-h3-max, the complete submitted text must be at most 7000 characters, including spaces, newlines and automatically appended subject, shot and reference-binding descriptions. Budget for those descriptions while writing the prompt; keep them concise, preserve required events and reference mappings, and check the complete text length before submitting.",
        },
        proxy_video={
            "type": ["string", "null"],
            "description": "Blender proxy with sequence.json, or null for interactive reference-only continuation.",
        },
        reference_videos=REFERENCE_LIST,
        reference_images=REFERENCE_LIST,
        duration_seconds={
            "type": ["integer", "null"],
            "minimum": 1,
            "description": "Current-round duration; null inherits proxy or current interaction duration.",
        },
        shot_manifest_path={
            "type": ["string", "null"],
            "description": "Proxy manifest; may be null when continuing from only images/videos.",
        },
        revision={"type": "integer", "minimum": 1},
    ),
    tool(
        "video_tail_frame",
        "Extract and return the actual last frame of a video as a reusable reference image.",
        path=PATH,
    ),
    tool(
        "record_video_review",
        "Record the root's preliminary review of an actual video_model result against the ORIGINAL request, proxy, references and PE. Acceptance requires passed technical checks, unchanged inputs and collected actual video pixels. Independent acceptance is required until the three-repair limit; then finish directly with remaining issues. On rejection, revise Blender/proxy/references/PE or resume the responsible child; then regenerate.",
        path=PATH,
        accepted={"type": "boolean"},
        content=STRING,
        identity_consistency=STRING,
        continuity=STRING,
        prompt_alignment=STRING,
        issues={"type": "array", "items": STRING},
    ),
]
