from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from coda_davinci.runtime.config import Settings, instructions
from coda_davinci.runtime.skill_registry import SkillRegistry
from coda_davinci.tools.workspace import WorkspaceTools
from coda_davinci.workflow.generation_routing import route_from_assessment, routed_settings
from coda_davinci.agents.acceptance import Acceptance


@pytest.fixture
def settings():
    return Settings(
        model="test-model",
        blender=Path("/unused/blender"),
        blender_version="4.5",
        engine="CYCLES",
        sandbox=False,
    )


def test_routes_respect_control_and_uncertainty():
    task = "A dog runs naturally through a meadow."
    assessment = dict(
        natural_motion_primary=True,
        proxy_rigidity_risk=True,
        requires_explicit_control=False,
        uncertain=False,
        evidence=["runs naturally"],
        reason="Natural gait is central.",
    )
    assert route_from_assessment(assessment, task) == "direct"
    assert (
        route_from_assessment({**assessment, "requires_explicit_control": True}, task) == "blender"
    )
    assert route_from_assessment({**assessment, "uncertain": True}, task) == "blender"
    with pytest.raises(ValueError, match="quote"):
        route_from_assessment({**assessment, "evidence": ["an invented request"]}, task)


def test_tools_and_packaged_skills(settings, tmp_path):
    for index, config in enumerate(
        (
            settings,
            routed_settings(settings, "direct"),
            replace(
                settings,
                acceptance_reviewer=True,
                recursive=False,
                proxy_mode=False,
                skills=("video-acceptance",),
            ),
        )
    ):
        root = tmp_path / str(index)
        root.mkdir()
        tools = WorkspaceTools(root, config, goal="Move a ball.")
        try:
            names = {t["name"] for t in tools.schemas}
            assert all(callable(tools.handlers[name]) for name in names)
            if config.acceptance_reviewer:
                assert "submit_video_acceptance" in names
                assert not names.intersection({"write_file", "video_model", "finish"})
            elif config.generation_route == "direct":
                assert {"video_model", "request_video_acceptance"} <= names
                assert "run_blender" not in names
            else:
                assert {"run_blender", "render_sequence", "delegate_tasks", "video_model"} <= names
            assert "threejs" not in tools.skills.registry.skills
        finally:
            tools.close()
    registry = SkillRegistry.snapshot(tmp_path / "bundle", settings)
    assert (
        "cylinder torso and sphere head"
        in (registry.skills["scene-assembly"].root / "SKILL.md").read_text()
    )
    assert "4" in instructions(settings)


def test_acceptance_budget_and_inspection(settings, tmp_path):
    tools = WorkspaceTools(
        tmp_path, replace(settings, recursive=False, proxy_mode=False), goal="Move the ball."
    )
    criteria = [
        dict(
            id="motion",
            category="action",
            requirement="Ball moves",
            basis="Move the ball",
            check="Observe movement",
            tolerance="Minor surface defects",
        )
    ]
    tools.set_acceptance_criteria(criteria)

    def reject(config, workspace, goal):
        reviewer = WorkspaceTools(workspace, config, goal=goal)
        try:
            with pytest.raises(ValueError, match="actual candidate"):
                reviewer.submit_video_acceptance(
                    [dict(id="motion", status="rework", evidence="Still", repair="Move it")],
                    "Revise",
                )
            reviewer.video_media.delivered[("inputs/candidate.mp4", "test")] = {0, 1}
            return reviewer.submit_video_acceptance(
                [
                    dict(
                        id="motion",
                        status="rework",
                        evidence="The ball is still",
                        repair="Animate the path",
                    )
                ],
                "Revise",
            ).data
        finally:
            reviewer.close()

    try:
        with patch("coda_davinci.agents.acceptance.run_reviewer", reject):
            for char in ("a", "b", "c"):
                rid = char * 32
                video = tmp_path / "renders" / rid / "video.mp4"
                video.parent.mkdir(parents=True)
                video.write_bytes(b"fixture-video")
                assert not tools.request_video_acceptance(rid)["accepted"]
        resumed = Acceptance(tools, "Move the ball.")
        assert resumed.limit_reached()
        with patch("coda_davinci.agents.acceptance.run_reviewer") as reviewer:
            assert resumed.request(rid)["review_skipped"]
            reviewer.assert_not_called()
        assert resumed.require(rid)["delivery_reason"] == "rework_limit_reached"
    finally:
        tools.close()


def test_continuation_requires_previous_final_frame(settings, tmp_path):
    from coda_davinci.workflow.continuation import check_inputs
    from coda_davinci.media.artifacts import file_hash
    from coda_davinci.workflow.references import ReferenceImage

    image = tmp_path / "tail.png"
    image.write_bytes(b"tail-frame-fixture")
    meta = tmp_path / ".harness"
    meta.mkdir()
    (meta / "continuation.json").write_text(
        json.dumps({"round": 2, "path": image.name, "sha256": file_hash(image)})
    )
    tools = SimpleNamespace(root=tmp_path, context=SimpleNamespace(interaction_round=2))
    with pytest.raises(ValueError, match="actual previous tail frame"):
        check_inputs(tools, [], [])
    check_inputs(tools, [ReferenceImage("tail", image)], [])
