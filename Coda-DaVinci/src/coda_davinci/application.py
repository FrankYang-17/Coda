import logging
import os
from pathlib import Path
import tempfile
from datetime import datetime, timezone
from coda_davinci.agents.agent import save_json
from coda_davinci.agents.team import Team
from coda_davinci.providers.clients import model_client, session_runner
from coda_davinci.providers.video import SeedanceSettings
from coda_davinci.runtime.config import Settings
from coda_davinci.runtime.statistics import write_usage
from coda_davinci.workflow.generation_routing import select_route, routed_settings
from coda_davinci.workflow.references import prepare_reference_images
from coda_davinci.media.reference_videos import prepare_reference_videos
from coda_davinci.workflow.task_record import save_original
from coda_davinci.workflow.pipeline import run_video_pipeline
from coda_davinci.workflow.interactive import load_interactions, run_interactions


def generate(
    prompt,
    *,
    output_root,
    duration=8,
    reference_images=(),
    reference_videos=(),
    interactions=None,
    parallel_agents=3,
    parallel_renders=1,
):
    if not prompt.strip():
        raise ValueError("Prompt must contain text")
    api_key = os.environ["API_KEY"]
    video = SeedanceSettings.from_env(
        duration_seconds=duration,
        resolution=os.environ.get("VIDEO_RESOLUTION", "720p"),
        aspect_ratio=os.environ.get("VIDEO_ASPECT_RATIO", "16:9"),
    )
    protocol = os.environ.get("AGENT_PROTOCOL", "responses")
    if protocol not in {"responses", "chat", "anthropic", "gemini"}:
        raise ValueError("AGENT_PROTOCOL must be responses, chat, anthropic or gemini")
    settings = Settings.from_env(
        provider="anthropic" if protocol == "anthropic" else "openai",
        openai_api="chat" if protocol in {"chat", "gemini"} else "responses",
        video_settings=video,
        video_total_duration=duration,
        interactive=bool(interactions),
        review_policy="practical" if interactions else "standard",
        max_parallel_agents=parallel_agents,
        max_parallel_renders=parallel_renders,
        max_tokens=int(os.environ.get("AGENT_MAX_OUTPUT_TOKENS", "16384")),
    )
    turns = load_interactions(interactions) if interactions else None
    root = Path(output_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    episode = Path(tempfile.mkdtemp(prefix=f"{stamp}_", dir=root))
    workspace = episode / "scene"
    workspace.mkdir()
    team = None
    result = {"status": "error"}
    (client_class, runner) = session_runner(settings)

    def run_session(path, goal, context=None):
        active = context.team.settings if context else settings
        with model_client(client_class, active, api_key) as client:
            return runner(client, active, path, goal, context=context)

    def run_coding(goal):
        nonlocal team
        team = Team(settings, episode, run_session) if settings.recursive else None
        return run_session(workspace, goal, team.root if team else None)

    try:
        refs = prepare_reference_images(list(reference_images), workspace)
        (workspace / ".harness").mkdir(exist_ok=True)
        save_json(
            workspace / ".harness/original_reference_images.json",
            [{"label": r.label, "path": r.path.relative_to(workspace).as_posix()} for r in refs],
        )
        videos = prepare_reference_videos(
            list(reference_videos), workspace, settings, image_labels=[r.label for r in refs]
        )
        save_original(workspace, prompt, refs, videos)
        with model_client(client_class, settings, api_key) as client:
            route = select_route(client, settings, workspace, prompt, refs, videos)
        settings = routed_settings(settings, route)
        save_json(
            episode / "episode.json",
            {
                "prompt": prompt,
                "model": settings.model,
                "video_model": video.model,
                "route": route,
                "duration_seconds": duration,
                "interactive": bool(turns),
            },
        )
        logging.info("Workspace: %s; route: %s", workspace, route)
        with model_client(client_class, settings, api_key) as client:
            if turns:
                team = Team(settings, episode, run_session)
                result = run_interactions(
                    client, settings, workspace, prompt, turns, refs, videos, team, run_session
                )
            else:
                result = run_video_pipeline(
                    client,
                    settings,
                    workspace,
                    prompt,
                    refs,
                    run_coding,
                    video,
                    reference_videos=videos,
                )
        return (episode, result)
    except KeyboardInterrupt:
        result = {"status": "interrupted"}
        if team:
            team.cancelled.set()
        raise
    except Exception as exc:
        result = {"status": "error", "type": type(exc).__name__, "message": str(exc)}
        raise
    finally:
        if team:
            team.close()
            (team.root.status, team.root.state) = (result["status"], "idle")
            team.save_report()
        save_json(episode / "episode_result.json", result)
        write_usage(episode)
