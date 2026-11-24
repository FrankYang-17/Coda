import json
from urllib.parse import urlsplit

from coda_davinci.providers.video import SeedanceSettings, SeedanceClient, run_seedance_generation


class Response:
    def __init__(self, value, content_type="application/json"):
        self.data = value if isinstance(value, bytes) else json.dumps(value).encode()
        self.headers = {"Content-Type": content_type}
        self.status = 200
        self.offset = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, size=-1):
        end = len(self.data) if size < 0 else self.offset + size
        value = self.data[self.offset : end]
        self.offset += len(value)
        return value


def test_reference_upload_generation_and_resume(tmp_path):
    calls = []

    def opener(request, timeout):
        path = urlsplit(request.full_url).path
        calls.append((request.method, path))
        if request.full_url.startswith("https://storage.example"):
            assert not request.has_header("Authorization")
        if path == "/v1/media/uploads":
            return Response({"id": "u1", "upload_url": "https://storage.example/upload"})
        if path == "/upload":
            assert request.data.read()
            return Response(b"")
        if path == "/v1/media/uploads/u1/complete":
            return Response({"status": "ready", "https_url": "https://storage.example/ref"})
        if path == "/v1/videos/generations":
            payload = json.loads(request.data)
            assert payload["duration_seconds"] == 8
            assert {x.get("role") for x in payload["content"]} >= {
                "reference_image",
                "reference_video",
            }
            assert request.get_header("Idempotency-key")
            return Response({"id": "task-1", "status": "queued"})
        if path == "/v1/videos/generations/task-1":
            return Response(
                {
                    "id": "task-1",
                    "status": "completed",
                    "video_url": "https://storage.example/result",
                }
            )
        if path == "/result":
            return Response(b"fixture-output", "video/mp4")
        raise AssertionError((request.method, path))

    settings = SeedanceSettings(
        "https://gateway.example",
        "fixture-key",
        "video-model",
        duration_seconds=8,
        poll_interval=0.001,
    )
    image, proxy = tmp_path / "ref.png", tmp_path / "proxy.mp4"
    image.write_bytes(b"image-fixture")
    proxy.write_bytes(b"proxy-fixture")
    result = run_seedance_generation(
        settings,
        tmp_path,
        "Move the red proxy.",
        client=SeedanceClient(settings, opener=opener),
        reference_images=[("scene", image)],
        reference_video=[proxy],
        proxy_video=proxy,
    )
    assert result["status"] == "completed"
    output = tmp_path / result["videos"][0]["path"]
    assert output.suffix == ".mp4" and output.read_bytes() == b"fixture-output"
    calls.clear()
    result = run_seedance_generation(
        settings,
        tmp_path,
        "Move the red proxy.",
        client=SeedanceClient(settings, opener=opener),
        task_id="task-1",
    )
    assert result["status"] == "completed"
    assert not any(method == "POST" for method, _ in calls)


def test_text_payload_and_model_config(monkeypatch):
    monkeypatch.setenv("VIDEO_MODEL_BASE_URL", "https://gateway.example")
    monkeypatch.setenv("VIDEO_MODEL_API_KEY", "fixture-key")
    monkeypatch.setenv("VIDEO_MODEL", "my-model")
    settings = SeedanceSettings.from_env(duration_seconds=8)
    payload = settings.payload("A landscape.")
    assert payload["prompt"] == "A landscape." and payload["model"] == "my-model"
    assert "content" not in payload
