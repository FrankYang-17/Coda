"""Run case interactions sequentially in one workspace with native root-history reuse."""

from dataclasses import replace
import json
import logging
from pathlib import Path
import shutil
from coda_davinci.workflow.final_video_delivery import load_final_video_delivery
from coda_davinci.media.reference_videos import reference_video_brief
from coda_davinci.media.video_media import save


def load_interactions(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    turns = data.get("turns")
    if not isinstance(turns, list) or not turns:
        raise ValueError("interaction-file requires a nonempty turns array")
    for index, turn in enumerate(turns, 1):
        if (
            not isinstance(turn, dict)
            or turn.get("turn") != index
            or (not isinstance(turn.get("prompt"), str))
            or (not turn["prompt"].strip())
            or (type(turn.get("duration_seconds")) is not int)
            or (turn["duration_seconds"] <= 0)
        ):
            raise ValueError(
                "Interactions require consecutive turn numbers, a prompt and positive duration_seconds"
            )
    return turns


def run_interactions(
    client, settings, workspace, base_prompt, turns, references, reference_videos, team, run_session
):
    """Only the controller receives future turns. The agent sees one new turn at a time."""
    meta = workspace / ".harness"
    meta.mkdir(exist_ok=True)
    result = {
        "status": "running",
        "workflow": "interactive",
        "prompt_author": "agent",
        "round_results": [],
    }
    previous = None
    number = 0
    try:
        for turn in turns:
            number = turn["turn"]
            from coda_davinci.workflow.task_record import save_original

            save_original(workspace, turn["prompt"], references, reference_videos, number)
            logging.info("Interaction %d starting; reusing %s", number, workspace)
            team.settings = replace(settings, video_total_duration=turn["duration_seconds"])
            if number == 1:
                team.root.interaction_round = 1
            else:
                team.resume_root(number)
            goal = (
                f"CURRENT INTERACTION ROUND {number}\n"
                + turn["prompt"]
                + f"\nGenerate approximately {turn['duration_seconds']} seconds for THIS interaction only. Earlier actions are completed history, not instructions to repeat. Do not anticipate future turns."
            )
            if number == 1:
                goal = "Initial scene and identities:\n" + base_prompt + "\n\n" + goal
                goal += "\nInitial image files:\n" + json.dumps(
                    [
                        {"label": r.label, "path": r.path.relative_to(workspace).as_posix()}
                        for r in references
                    ],
                    ensure_ascii=False,
                )
                goal += reference_video_brief(reference_videos, workspace)
            else:
                continuation = (
                    "\nContinue the SAME scene/scripts/subject registry and conversation; load the saved .blend and modify it instead of rebuilding the scene. The previous accepted video is "
                    + previous["final_video"]
                    + ". You may pass that whole video with an updated proxy, or extract its tail frame and generate from images only. Choose the useful references yourself; a new proxy upload is optional."
                )
                continuation = (
                    continuation.split(". You may pass", 1)[0]
                    + ". Follow the configured proxy and continuation policies; do not choose a different route."
                )
                goal += continuation
                from coda_davinci.workflow.continuation import tail_frame

                tail = tail_frame(workspace, team.settings, previous["final_video"], number)
                goal += (
                    "\nCONTROLLER REQUIRED TAIL FRAME: "
                    + json.dumps(tail)
                    + "\nUse this image, not a full prior video or other historical frames."
                )
            from coda_davinci.workflow.modeling_policy import system_guidance

            goal += system_guidance(settings)
            goal += "\nSave reusable scene state in scene_state.json (the .blend path, current frame/camera and relevant object states). A missing cosmetic detail is not a reason for endless regeneration. Inspect a useful preview, deliver a usable best effort with honest remaining_issues, and finish THIS round. Do not run a judge or numerical rubric. Root finish is a round checkpoint, not case shutdown."
            audit = workspace / "turns" / f"{number:03d}"
            audit.mkdir(parents=True, exist_ok=True)
            save(audit / "instruction.json", turn)
            coding = run_session(workspace, goal, team.root)
            team.root.status = coding["status"]
            team.root.state = "idle"
            save(audit / "coding_result.json", coding)
            if coding["status"] != "completed":
                result["status"] = coding["status"]
                result["failed_round"] = number
                return result
            if coding.get("interaction_round") != number:
                raise ValueError("The root returned a delivery from a different interaction")
            load_final_video_delivery(workspace, coding)
            row = {"turn": number, **coding}
            result["round_results"].append(row)
            previous = coding
            for name in ("request.json", "result.json"):
                source = meta / name
                if source.exists():
                    shutil.copyfile(source, audit / name)
            state = workspace / "scene_state.json"
            if state.is_file():
                shutil.copyfile(state, audit / "scene_state.json")
            logging.info(
                "Interaction %d delivered; remaining issues=%d",
                number,
                len(coding.get("remaining_issues", [])),
            )
            team.save_report()
            save(meta / "pipeline_result.json", result)
        result.update(
            status="completed",
            final_video=previous["final_video"],
            video_render={
                "status": "completed",
                "videos": [
                    {"turn": r["turn"], "path": r["final_video"]} for r in result["round_results"]
                ],
            },
        )
        return result
    except KeyboardInterrupt:
        result["status"] = "interrupted"
        raise
    except Exception as exc:
        result.update(
            status="error", type=type(exc).__name__, message=str(exc), failed_round=number
        )
        (team.root.status, team.root.state) = ("error", "idle")
        return result
    finally:
        save(meta / "pipeline_result.json", result)
