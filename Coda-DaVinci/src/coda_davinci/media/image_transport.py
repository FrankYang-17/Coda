from PIL import Image

"""Private image compression for model requests; source paths and artifacts stay intact."""

import base64
import hashlib
from io import BytesIO
from pathlib import Path
import tempfile
import threading
import uuid

_CACHE = None
_LOCK = threading.Lock()
_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


def _jpeg_bytes(png: bytes) -> bytes:
    """Cache by content, so overwriting a PNG never reuses an outdated preview."""
    global _CACHE
    try:
        with _LOCK:
            if _CACHE is None:
                rank = 0
                _CACHE = tempfile.TemporaryDirectory(
                    prefix=f"code2video_images_rank{rank}_", dir="/tmp"
                )
            key = hashlib.sha256(b"jpeg-q90-s0-white-v1\x00" + png).hexdigest()
            target = Path(_CACHE.name) / f"{key}.jpg"
            if not target.is_file():
                pending = target.with_name(f"{key}.{uuid.uuid4().hex}.tmp")
                try:
                    with Image.open(BytesIO(png)) as source:
                        if "A" in source.getbands() or "transparency" in source.info:
                            rgba = source.convert("RGBA")
                            rgb = Image.new("RGB", source.size, "white")
                            rgb.paste(rgba, mask=rgba.getchannel("A"))
                        else:
                            rgb = source.convert("RGB")
                        rgb.save(pending, format="JPEG", quality=90, subsampling=0, optimize=True)
                    pending.replace(target)
                finally:
                    pending.unlink(missing_ok=True)
            return target.read_bytes()
    except Exception as exc:
        raise ValueError(f"Could not prepare PNG for model input ({type(exc).__name__})") from None


def image_data_url(path: Path) -> str:
    mime = _MIME.get(path.suffix.lower())
    if mime is None:
        raise ValueError("Model images must be PNG, JPEG or WebP")
    data = path.read_bytes()
    if mime == "image/png":
        (data, mime) = (_jpeg_bytes(data), "image/jpeg")
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def compress_history_images(history) -> None:
    """Convert legacy inline PNGs when resuming, preserving labels, paths and call IDs."""

    def convert_url(url):
        if not isinstance(url, str) or not url.startswith("data:image/png;base64,"):
            return url
        data = base64.b64decode(url.split(",", 1)[1], validate=True)
        return "data:image/jpeg;base64," + base64.b64encode(_jpeg_bytes(data)).decode("ascii")

    def visit(value):
        if isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            kind = value.get("type")
            if kind == "input_image" and "image_url" in value:
                value["image_url"] = convert_url(value["image_url"])
            elif kind == "image_url" and isinstance(value.get("image_url"), dict):
                image = value["image_url"]
                if "url" in image:
                    image["url"] = convert_url(image["url"])
            elif kind == "image" and isinstance(value.get("source"), dict):
                source = value["source"]
                if source.get("type") == "base64" and source.get("media_type") == "image/png":
                    data = base64.b64decode(source["data"], validate=True)
                    source.update(
                        media_type="image/jpeg",
                        data=base64.b64encode(_jpeg_bytes(data)).decode("ascii"),
                    )
            for item in value.values():
                if isinstance(item, (dict, list)):
                    visit(item)

    visit(history)
