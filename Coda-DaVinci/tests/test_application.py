from contextlib import nullcontext
import json

from coda_davinci import application
from coda_davinci.cli import main


def test_application_wires_default_route_and_delivery(tmp_path, monkeypatch):
    for key, value in {
        "MODEL": "test-model",
        "API_KEY": "test",
        "VIDEO_MODEL_BASE_URL": "https://gateway.example",
        "VIDEO_MODEL_API_KEY": "test",
        "VIDEO_MODEL": "test-video",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(application, "model_client", lambda *a, **kw: nullcontext(object()))
    monkeypatch.setattr(application, "select_route", lambda *a: "direct")

    def runner(client, settings, workspace, goal, context=None):
        assert settings.generation_route == "direct"
        assert "direct-video" in goal
        target = workspace / "Final View"
        target.mkdir()
        (target / "video.mp4").write_bytes(b"fixture")
        return {"status": "completed", "final_video": "Final View/video.mp4"}

    monkeypatch.setattr(application, "session_runner", lambda _: (object, runner))
    monkeypatch.setattr(
        "coda_davinci.workflow.pipeline.load_final_video_delivery",
        lambda workspace, result: {"video_render": {"status": "completed", "videos": []}},
    )
    episode, result = application.generate("A dog runs naturally.", output_root=tmp_path)
    assert result["status"] == "completed"
    assert json.loads((episode / "episode.json").read_text())["route"] == "direct"
    assert (episode / "episode_result.json").is_file()


def test_help_and_doctor_without_credentials(capsys, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for key in ("API_KEY", "MODEL", "VIDEO_MODEL_API_KEY", "IMAGE_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    assert main(["doctor"]) == 1
    assert "MISSING" in capsys.readouterr().out
