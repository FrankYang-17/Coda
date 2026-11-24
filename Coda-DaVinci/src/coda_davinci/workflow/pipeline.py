"""Root-owned video generation, inspection and repair."""

import json
import logging
from pathlib import Path
from coda_davinci.agents.agent import save_json
from coda_davinci.workflow.final_video_delivery import load_final_video_delivery
from coda_davinci.media.reference_videos import reference_video_brief


def run_video_pipeline(
    client,
    settings,
    workspace: Path,
    user_prompt: str,
    references,
    run_coding,
    video_settings,
    reference_videos=(),
):
    meta = workspace / ".harness"
    meta.mkdir(exist_ok=True)
    result = {"status": "agent_error", "workflow": "agent_full_video", "prompt_author": "agent"}
    try:
        direct = getattr(settings, "generation_route", "blender") == "direct"
        goal = user_prompt
        if references:
            goal += "\nReference image snapshots (preserve labels):\n" + json.dumps(
                [
                    {"label": item.label, "path": item.path.relative_to(workspace).as_posix()}
                    for item in references
                ],
                ensure_ascii=False,
            )
        goal += reference_video_brief(reference_videos, workspace)
        goal += "\nBefore construction, read acceptance-criteria and register your task-specific acceptance criteria. After building and inspecting the complete Blender proxy, read video-pe and call video_model with your generation prompt and actual reference media. Inspect the returned final video; revise Blender/proxy/references/PE or resume responsible children until it meets the request. Call request_video_acceptance for the final render_uuid and repair all rework findings before requesting acceptance again. Allow at most three repairs: after the third rejection, perform the final repair and finish directly with remaining_issues, skipping further acceptance."
        if direct:
            goal = user_prompt
            if references:
                goal += "\nAuthorized reference images:\n" + json.dumps(
                    [
                        {"label": item.label, "path": item.path.relative_to(workspace).as_posix()}
                        for item in references
                    ],
                    ensure_ascii=False,
                )
            goal += reference_video_brief(reference_videos, workspace)
            goal += "\nRead direct-video and acceptance-criteria. Plan the actions and camera, generate the complete video, inspect it and complete acceptance before finish."
        if settings.video_total_duration is not None:
            goal += f"\nRequired complete video duration: {settings.video_total_duration} seconds."
        result["status"] = "agent_error"
        coding = run_coding(goal)
        result.update(coding)
        result["coding_result"] = coding
        if coding["status"] != "completed":
            return result
        result["status"] = "video_delivery_error"
        delivery = load_final_video_delivery(workspace, coding)
        result.update(status="completed", video_render=delivery["video_render"])
        return result
    except KeyboardInterrupt:
        result["status"] = "interrupted"
        raise
    except Exception as exc:
        logging.exception("Video workflow failed at %s", result["status"])
        result.update(type=type(exc).__name__, message=str(exc))
        return result
    finally:
        save_json(meta / "pipeline_result.json", result)
