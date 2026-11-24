import base64
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import MagicMock

from PIL import Image

from coda_davinci.providers.images import generate_reference


def test_generate_and_edit_reference(tmp_path, monkeypatch):
    for key, value in {
        "IMAGE_MODEL": "image-model",
        "IMAGE_API_KEY": "fixture-key",
        "IMAGE_BASE_URL": "https://images.example/v1",
    }.items():
        monkeypatch.setenv(key, value)
    buffer = BytesIO()
    Image.new("RGB", (32, 32), "white").save(buffer, format="PNG")
    response = SimpleNamespace(
        data=[SimpleNamespace(b64_json=base64.b64encode(buffer.getvalue()).decode())],
        _request_id="image-fixture",
    )
    client = MagicMock()
    client.__enter__.return_value = client
    client.images.generate.return_value = response
    client.images.edit.return_value = response
    monkeypatch.setattr("openai.OpenAI", lambda **kwargs: client)
    scene, _ = generate_reference(
        tmp_path / "scene", "", "", "A studio.", layout="scene-board", subject_kind="scene"
    )
    edited, _ = generate_reference(tmp_path / "edit", "A person", "A coat", "A portrait.", scene)
    assert scene.is_file() and edited.is_file()
    client.images.generate.assert_called_once()
    client.images.edit.assert_called_once()
