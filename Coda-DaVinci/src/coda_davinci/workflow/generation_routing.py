import json
from dataclasses import replace
from coda_davinci.workflow import references as pe
from coda_davinci.media.video_media import save

RULES = "Choose the generation workflow from the original task and supplied visual references.\nEvaluate required capabilities, independent of dataset names, case IDs and evaluation labels.\nDirect generation is appropriate only when natural articulated motion is the main challenge\nand a simplified geometric proxy would constrain its quality: dance, expressive body motion,\nanimal gait, or many-jointed robotic motion whose primary goal is natural appearance.\nPeople, animals or machines merely appearing in a scene are insufficient reasons.\nKeep Blender for exact trajectories, speed or period relations, force/contact/collision\nconstraints, mechanically constrained assemblies, scene revisits, persistent object states,\nstrict spatial relations, counts/layouts, exact text/UI layouts, or precise event order.\nWhen these controls coexist with natural motion, explicit control takes priority.\nSimple gears, linkages and rigid-body motion benefit from Blender. Mark uncertainty explicitly.\nReturn one JSON object with these fields:\nnatural_motion_primary: boolean; proxy_rigidity_risk: boolean;\nrequires_explicit_control: boolean; uncertain: boolean;\nevidence: a nonempty list of exact quotes from the original task;\nreason: a short explanation grounded in those requirements.\n"
DIRECT_TOOLS = {
    "write_file",
    "edit_file",
    "read_file",
    "list_files",
    "view_image",
    "video_info",
    "view_video_frame",
    "view_video_clip",
    "wait_tool",
    "interrupt_tool",
    "list_skills",
    "read_skill",
    "video_model",
    "inspect_video",
    "record_video_review",
    "set_acceptance_criteria",
    "request_video_acceptance",
    "finish",
}
DIRECT_SKILLS = {"direct-video", "acceptance-criteria", "video-acceptance"}


def route_from_assessment(assessment, task):
    for name in (
        "natural_motion_primary",
        "proxy_rigidity_risk",
        "requires_explicit_control",
        "uncertain",
    ):
        if type(assessment.get(name)) is not bool:
            raise ValueError(f"Routing assessment requires boolean {name}")
    evidence = assessment.get("evidence")
    if (
        not isinstance(evidence, list)
        or not evidence
        or any(
            (
                not isinstance(quote, str) or not quote.strip() or quote not in task
                for quote in evidence
            )
        )
    ):
        raise ValueError("Routing evidence must quote the original task")
    if not isinstance(assessment.get("reason"), str) or not assessment["reason"].strip():
        raise ValueError("Routing assessment requires a reason")
    return (
        "direct"
        if assessment["natural_motion_primary"]
        and assessment["proxy_rigidity_risk"]
        and (not assessment["requires_explicit_control"])
        and (not assessment["uncertain"])
        else "blender"
    )


def select_route(client, settings, workspace, task, references=(), reference_videos=()):
    requested = settings.generation_route
    path = workspace / ".harness" / "route_decision.json"
    if path.exists():
        record = json.loads(path.read_text())
        if record["task"] != task or record["requested"] != requested:
            raise ValueError("Generation route is fixed for the original task")
        return record["selected"]
    record = {"requested": requested, "task": task, "selected": requested}
    if requested == "auto":
        if (
            settings.interactive
            or "default" != "default"
            or (settings.video_settings and settings.video_settings.reference_task_type == "edit")
        ):
            record.update(
                selected="blender",
                reason="The configured workflow requires explicit scene continuity or proxy-based control.",
            )
        else:
            system = RULES
            observations = [pe.ImageObservation(item.label, item.path) for item in references]
            for video in reference_videos:
                observations.extend(
                    (
                        pe.ImageObservation(item["label"], workspace / item["path"])
                        for item in video.observations
                    )
                )
            send = (
                pe._anthropic_call
                if settings.provider == "anthropic"
                else pe._openai_chat_call
                if settings.openai_api == "chat"
                else pe._openai_responses_call
            )
            save(
                workspace / ".harness" / "route_request.json",
                {"instructions": system, "task": task, "model": settings.model},
            )
            (text, response) = send(client, settings, system, {"task": task}, observations)
            save(workspace / ".harness" / "route_response.json", pe._response_dump(response))
            assessment = pe.parse_pe_json(text)
            record.update(
                selected=route_from_assessment(assessment, task),
                assessment=assessment,
                model=settings.model,
            )
    save(path, record)
    return record["selected"]


def routed_settings(settings, route):
    if route == "blender":
        return replace(settings, generation_route="blender")
    if route != "direct":
        raise ValueError(f"Unresolved generation route: {route}")
    if settings.interactive or "default" != "default":
        raise ValueError(
            "Direct generation requires a single task and the default agent-authored PE"
        )
    if settings.video_settings and settings.video_settings.reference_task_type == "edit":
        raise ValueError("Direct generation requires the reference video API mode")
    return replace(
        settings,
        generation_route="direct",
        recursive=False,
        skills=("direct-video", "acceptance-criteria"),
    )


def direct_instructions(settings):
    text = "You are a video generation agent. Read direct-video and acceptance-criteria.\nTurn the original task into a compact action and camera plan, choose authorized reference media,\ngenerate a complete video with video_model, inspect actual frames, and repair concrete failures.\nUse proxy_video=null and shot_manifest_path=null; preserve the requested duration and input roles.\nRecord a visual review and request independent acceptance. After at most three repairs, inspect\nand deliver the best available generated video with remaining issues through finish.\nKeep job/revision IDs and resume unchanged submissions. Save planning and review notes in files.\n"
    if settings.video_settings:
        text += f"Video model: {settings.video_settings.model}. Resolution: {settings.video_settings.resolution}.\n"
    return text
