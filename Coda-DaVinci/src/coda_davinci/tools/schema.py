"""Model-facing file/render tools and optional recursive task contracts."""

from coda_davinci.tools.tool_jobs import CONTROL_TOOLS, MAX_YIELD_MS, START_YIELD_MS, WAIT_YIELD_MS


def yield_time(default):
    return {
        "type": ["integer", "null"],
        "minimum": 0,
        "maximum": MAX_YIELD_MS,
        "description": f"Wait up to this many milliseconds before returning control; null uses {default}. 0 polls immediately. Does not cancel the job or change its hard timeout.",
    }


def tool(tool_name, description, **properties):
    if tool_name not in CONTROL_TOOLS:
        properties.setdefault("yield_time_ms", yield_time(START_YIELD_MS))
        description += " Runs as a background job; default yield_time_ms is 0. If running, use wait_tool(job_id) to collect the result before dependent work."
    return {
        "type": "function",
        "name": tool_name,
        "description": description,
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        },
    }


STRING = {"type": "string"}
PATH = {"type": "string", "description": "Path relative to the episode workspace."}
THREADS = {
    "type": ["integer", "null"],
    "minimum": 0,
    "maximum": 1024,
    "description": "Blender CPU thread setting per process (--threads). null leaves existing/default selection unchanged; 0 uses system processor count. Positive values set the count. Does not change GPU selection or FFmpeg threads. Tune alongside process concurrency.",
}
TOOLS = [
    tool(
        "wait_tool",
        "Poll a tool job or wait up to yield_time_ms (default 10000ms). Returns current status and log tail, or the final result. Completion or background updates may return earlier. Use the original job_id; does not start another process.",
        job_id={"type": "string", "pattern": "^[0-9a-f]{32}$"},
        yield_time_ms=yield_time(WAIT_YIELD_MS),
    ),
    tool(
        "interrupt_tool",
        "Interrupt only your own running tool job and its current process group by job_id. Uses the recorded PID, preserves logs and partial files, and does not end the agent or task tree. Returns immediately; interrupt_requested is not yet completion. Delegation cancellation propagates to its children. Completed jobs are returned unchanged. Never pass a PID as job_id.",
        job_id={"type": "string", "pattern": "^[0-9a-f]{32}$"},
    ),
    tool(
        "write_file",
        "Create or replace a UTF-8 file. Existing content is backed up. Python is syntax checked. When writing render.py or other setup scripts for render_image/render_sequence, configure the scene and render settings but do not call bpy.ops.render.render: the harness starts rendering after setup. Standalone preview scripts executed through run_blender may call render themselves.",
        path=PATH,
        content=STRING,
    ),
    tool(
        "edit_file",
        "Replace one exact, unique text occurrence. Read the file first; old_text must match exactly.",
        path=PATH,
        old_text=STRING,
        new_text=STRING,
    ),
    tool(
        "read_file",
        "Read a UTF-8 file slice, including code or logs. Offset is zero-based characters.",
        path=PATH,
        offset={"type": "integer", "minimum": 0},
        limit={"type": "integer", "minimum": 1, "maximum": 40000},
    ),
    tool(
        "list_files",
        "List immediate directory entries with sizes. Use '.' for the workspace.",
        path=PATH,
    ),
    tool(
        "run_blender",
        "Run Python scripts in order in a new background Blender process. Returns logs, not images. Use wait_tool or interrupt_tool for the job; do not relaunch it to check progress.",
        scripts={"type": "array", "items": STRING, "minItems": 1},
        blend_file={
            "type": ["string", "null"],
            "description": "Existing workspace .blend or null for factory startup.",
        },
        threads=THREADS,
        yield_time_ms=yield_time(START_YIELD_MS),
    ),
    tool(
        "render_image",
        "Run bpy setup scripts, then render the current scene frame to one PNG. Use this for still-image goals; it does not create a video or frame sequence.",
        scripts={"type": "array", "items": STRING, "minItems": 1},
        blend_file={
            "type": ["string", "null"],
            "description": "Existing workspace .blend or null for factory startup.",
        },
        threads=THREADS,
        yield_time_ms=yield_time(START_YIELD_MS),
    ),
    tool(
        "render_sequence",
        "Run bpy setup scripts, then render the configured animation to PNG frames only. Returns frames directory, sequence manifest, fps and frame_count. No MP4 is encoded; use encode_video afterwards.",
        scripts={"type": "array", "items": STRING, "minItems": 1},
        blend_file={
            "type": ["string", "null"],
            "description": "Existing workspace .blend or null for factory startup.",
        },
        threads=THREADS,
        yield_time_ms=yield_time(START_YIELD_MS),
    ),
    tool(
        "encode_video",
        "Use FFmpeg to encode one or more PNG frame directories into one MP4. Directory order is exactly the supplied order. Within each directory, use adjacent sequence.json order or numeric filename order if no manifest exists. Copies frames into a new self-contained render package. Wait for all source renders before calling. Returns a render_uuid usable with finish; inspect this version first.",
        frame_dirs={
            "type": "array",
            "items": PATH,
            "minItems": 1,
            "description": "Ordered frame directories, e.g. the frames values returned by render_sequence.",
        },
        fps={
            "type": ["number", "null"],
            "exclusiveMinimum": 0,
            "description": "Output playback FPS. null inherits matching manifest FPS from all inputs. Required when manifests are absent or FPS differs. No frame dropping or interpolation.",
        },
    ),
    tool(
        "view_image",
        "Return a saved PNG/JPEG/WebP as actual visual input, not just its filename.",
        path=PATH,
    ),
    tool(
        "video_info",
        "Inspect video duration, frame count and FPS before choosing preview intervals. External videos also report dimensions. No images or audio returned.",
        path=PATH,
    ),
    tool(
        "view_video_frame",
        "Read original rendered frames using adjacent sequence.json, or decode frames from an external video when no manifest exists. Returns labeled images in requested order. Indices are zero-based positions in the manifest or decoded video, not Blender frame numbers. Missing indices are reported separately; there is no harness frame-count cap.",
        path={
            "type": "string",
            "description": "Workspace video.mp4 or sequence.json path. External videos are decoded; rendered sequences use their source frames and MP4 need not exist.",
        },
        frame_indices={"type": "array", "minItems": 1, "items": {"type": "integer", "minimum": 0}},
    ),
    tool(
        "view_video_clip",
        "Preview a time interval from rendered frames or an external video. Returns actual images with timestamps, sampled at preview fps; decodes external video as needed without changing video FPS; audio is not inspected. Samples start, start+1/fps, ... strictly before start+duration; clips at sequence end. Each sample selects the frame displayed at that time. Preview fps above source FPS is capped to avoid duplicates. For 24 FPS source, start=0, duration=2, fps=2 returns indices 0,12,24,36. Use short intervals and suitable preview fps to control image count; inspect suspicious intervals more densely.",
        path={"type": "string", "description": "Workspace video file or sequence.json path."},
        start_seconds={
            "type": "number",
            "minimum": 0,
            "description": "Start in playback seconds relative to the first frame of this sequence.",
        },
        duration_seconds={
            "type": "number",
            "exclusiveMinimum": 0,
            "description": "Requested clip duration, with an exclusive end time.",
        },
        fps={
            "type": "number",
            "exclusiveMinimum": 0,
            "description": "Preview sampling FPS only, not the source/render/encoding frame rate.",
        },
    ),
    tool(
        "finish",
        "Submit the image or animation render version you judge satisfactory. Copies renders/<render_uuid>/ to Final View/ at the episode root, retaining the original. Fails if Final View already exists.",
        render_uuid={
            "type": "string",
            "pattern": "^[0-9a-f]{32}$",
            "description": "The render_uuid returned by render_image (still) or encode_video (video) for the selected final version, not a path.",
        },
        summary=STRING,
        artifacts={"type": "array", "items": STRING, "minItems": 1},
        shot_manifest_path={
            "type": ["string", "null"],
            "description": "Workspace-relative JSON manifest authored by the root for the final video. Required in PE colored-proxy mode; null otherwise. Use the final encode_video render_uuid as render_id. Copied to Final View/shot_manifest.json and retained with the final delivery.",
        },
        remaining_issues={"type": "array", "items": STRING},
    ),
]
TASK = {
    "type": "object",
    "additionalProperties": False,
    "required": ["kind", "goal", "acceptance", "inputs", "skills"],
    "properties": {
        "kind": {"type": "string", "enum": ["asset", "camera", "review"]},
        "goal": {
            "type": "string",
            "description": "Scope, component interface, dimensions and requested result.",
        },
        "acceptance": {
            "type": "string",
            "description": "Concrete acceptance criteria and required inspection views.",
        },
        "inputs": {
            "type": "array",
            "items": STRING,
            "description": "Workspace files/directories to snapshot under child's inputs/. No history is inherited.",
        },
        "skills": {
            "type": "array",
            "items": STRING,
            "description": "Skill names to explicitly load in the child's initial context; [] lets it choose.",
        },
    },
}
TEAM_TOOLS = [
    tool(
        "delegate_tasks",
        "Delegate independent tasks concurrently in a background job. Child task_ids arrive in agent_tasks_started notifications and list_agents before delivery. Active direct children across all batches share this parent's child_slots limit. If the requested batch exceeds available slots, reject the whole batch without creating or queueing agents. Children can recursively delegate. Successful packages are copied into imports/<task_id>/. Read their manifests, assemble locally, and render/view the whole before completing. Failures are returned individually; no partial package is imported.",
        tasks={"type": "array", "items": TASK, "minItems": 1},
    ),
    tool(
        "resume_task",
        "Wake an idle direct child by task_id in this episode. Restores its native conversation history and workspace, applies feedback and new versioned input snapshots, and runs in the background. Final result includes the same task_id and new manifest path. Previous deliveries remain unchanged. Requires a free active-child slot; rejects instead of queueing when full. The child uses the same per-activation max-turns as all agents; no new total-task/depth slot is used.",
        task_id={"type": "string", "pattern": "^[0-9a-f]{32}$"},
        feedback=STRING,
        inputs={
            "type": "array",
            "items": STRING,
            "description": "New parent files/directories to snapshot; [] retains existing inputs.",
        },
    ),
    tool(
        "send_message",
        "Queue a message to your parent or direct child, including while it is working. Does not interrupt tools or an in-flight model response. Delivered separately before its next model request. Sending to an idle child does not wake it; use resume_task. Returns a message_id receipt, not acknowledgement.",
        task_id=STRING,
        message=STRING,
    ),
    tool(
        "list_agents",
        "Return status, task_id, activation and latest delivery for yourself, your parent and direct children, plus your child_slots limit, active count and available slots.",
    ),
    tool(
        "wait_agent",
        "Poll or wait for a direct child by task_id, default 10000ms; 0 polls. May return early when a message arrives. Does not start or resume the child.",
        task_id=STRING,
        yield_time_ms=yield_time(WAIT_YIELD_MS),
    ),
    tool(
        "interrupt_agent",
        "Request interruption of a direct child's current activation and descendants. Returns immediately; in-flight model requests cannot be forcibly stopped. Saved context remains. Wait until state=idle before resume_task. Does not interrupt siblings or the parent.",
        task_id=STRING,
    ),
]
SKILL_TOOLS = [
    tool(
        "list_skills",
        "Find available bundled skills by name or description; returns metadata, not instructions.",
        query=STRING,
        offset={"type": "integer", "minimum": 0},
        limit={"type": "integer", "minimum": 1, "maximum": 100},
    ),
    tool(
        "read_skill",
        "Load full skill instructions and make the package available read-only under .skills/<name>/. After reading SKILL.md, optionally read a supporting file. Scripts are not executed by this tool. Use existing Blender/image/file tools on the returned paths; long references can be paged with read_file.",
        name=STRING,
        path={
            "type": ["string", "null"],
            "description": "Path within the skill package, or null for SKILL.md.",
        },
    ),
]
SUBMIT_TASK = tool(
    "submit_task",
    "Finish a child task by submitting delivery/manifest.json. Read delivery-contract first. Snapshots the manifest and its files for the parent, without automatic delivery acceptance checks. The manifest must be a readable JSON object with a files list of package-relative paths. Follow delivery-contract and inspect your work before submitting; the parent reviews the result. The completion receipt includes task_id so the parent can resume this child later. Call this tool alone; root tasks use finish instead.",
    manifest_path=PATH,
)
SUBJECT_TOOLS = [
    tool(
        "register_subject",
        "Root agent only: register stable person/animal identity and outfit before delegating. Existing records require their current revision. IDs are shared by all agents.",
        subject_id=STRING,
        kind={"type": "string", "enum": ["person", "animal"]},
        identity_caption=STRING,
        outfit_id=STRING,
        outfit_caption=STRING,
        expected_revision={"type": ["integer", "null"]},
    ),
    tool(
        "list_subjects",
        "List episode-wide subjects, captions and revisions. Use get_subject to copy reference images into your workspace.",
    ),
    tool(
        "get_subject",
        "Read an existing stable identity and materialize its references inside your own inputs/subjects folder.",
        subject_id=STRING,
    ),
    tool(
        "select_subject_identity_reference",
        "Root only: after inspecting a bound outfit reference, select it as the identity anchor for future outfits and video requests. Preserve the same subject identity; previous anchors stay in history. Use this to adopt an approved character sheet instead of an old single view.",
        subject_id=STRING,
        outfit_id=STRING,
        expected_revision={"type": "integer"},
    ),
    tool(
        "bind_subject_reference",
        "Bind an existing image of one identity (single view or multi-view character sheet). Declare its actual layout; null means single. The first binding becomes the stable identity anchor. Revision check prevents stale updates.",
        subject_id=STRING,
        outfit_id=STRING,
        path=PATH,
        expected_revision={"type": "integer"},
        layout={"type": ["string", "null"], "enum": ["single", "character-sheet", None]},
    ),
    tool(
        "generate_subject_reference",
        "Generate and bind an appearance reference using GPT_IMAGE_MODEL (fallback IMAGE_MODEL). Null layout chooses character-sheet for people and single for animals. Null reference_path automatically edits an existing outfit reference or stable identity anchor, including for new outfits. Existing images are reused unless regenerate=true; layout mismatch is reported without silently generating again. Never repeat unresolved submissions.",
        subject_id=STRING,
        outfit_id=STRING,
        prompt=STRING,
        reference_path={"type": ["string", "null"]},
        regenerate={"type": "boolean"},
        layout={"type": ["string", "null"], "enum": ["single", "character-sheet", None]},
    ),
    tool(
        "generate_scene_reference",
        "Generate a finished scene appearance reference with the repository image-model interface. Use this for text-to-video cases before detailed Blender modeling; the root must inspect the returned image and pass its saved path to video_model. Include the requested layout, major objects, material, lighting and spatial relationships in prompt. Existing labels are reused unless regenerate=true; never repeat an unresolved submission.",
        label=STRING,
        prompt=STRING,
        reference_path={"type": ["string", "null"]},
        regenerate={"type": "boolean"},
    ),
]
TOOLS.extend(SUBJECT_TOOLS)
