import json
from pathlib import Path

RESOURCES = Path(__file__).parents[1] / "resources"


def enabled(settings):
    return settings.generation_route != "direct"


def capabilities(settings):
    return json.loads((RESOURCES / "capabilities.json").read_text())


def system_guidance(settings):
    return (RESOURCES / "modeling.md").read_text() if enabled(settings) else ""


def freeze(workspace, settings):
    from coda_davinci.media.video_media import save

    save(
        workspace / ".harness/modeling_policy.json",
        {
            "route": settings.generation_route,
            "capabilities": capabilities(settings) if enabled(settings) else None,
        },
    )


def submission_audit(tools, prompt, effective_prompt):
    return {
        "capabilities": capabilities(tools.settings),
        "authored_characters": len(prompt),
        "submitted_characters": len(effective_prompt),
    }
