"""Generate one appearance reference using the repository's image-model environment."""

import base64
import json
import math
import os
from pathlib import Path
import uuid
from urllib.request import urlopen
from PIL import Image


def generate_reference(
    directory,
    identity_caption,
    outfit_caption,
    prompt,
    reference=None,
    *,
    layout="single",
    subject_kind="person",
):
    from openai import OpenAI

    model = os.environ.get("IMAGE_MODEL", "").strip()
    key = os.environ.get("IMAGE_API_KEY", "").strip()
    base = os.environ.get("IMAGE_BASE_URL", "").rstrip("/")
    if not all((model, key, base)):
        raise ValueError("Set IMAGE_MODEL, IMAGE_API_KEY and IMAGE_BASE_URL")
    timeout = float(os.environ.get("IMAGE_API_TIMEOUT", "600"))
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("IMAGE_API_TIMEOUT must be a positive finite number of seconds")
    if layout not in ("single", "character-sheet", "scene-board"):
        raise ValueError("Reference layout must be single, character-sheet or scene-board")
    if subject_kind not in ("person", "animal", "scene"):
        raise ValueError("Subject kind must be person, animal or scene")
    default_size = "1536x1024" if layout in ("character-sheet", "scene-board") else "1024x1536"
    size = os.environ.get("IMAGE_SIZE", default_size)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / (uuid.uuid4().hex + ".png")
    presentation = (
        "Create a cinematic scene design reference for film preproduction. Show one coherent environment from a useful wide camera-facing view, with the major structures, objects, spatial layers, access paths and material relationships visible. Use the requested visual style with finished materials, lighting, scale and contact shadows. Include the repeated units and object proportions requested in the prompt, including required people or animals and their positions; keep the composition readable and free of captions, diagrams or unrelated scenery."
        if layout == "scene-board"
        else "Create a professional cinematic character design sheet for film preproduction. Every panel depicts the SAME character, not a cast of different people. Use a clean landscape layout: four full-body views (front, three-quarter, side and back); three clearly readable facial close-ups (neutral, gentle smile, focused expression); small wardrobe detail panels showing the requested outfit and footwear. Preserve the attached character facial identity and body proportions across all panels. Render clothing from the requested outfit caption; change the reference clothing when that caption explicitly differs. Preserve hairstyle unless an explicit requested change is given. Keep faces clear and unobscured, with consistent neutral lighting and a simple gray background. Use concise view labels and realistic character rendering. No other characters or scenery."
        if layout == "character-sheet"
        else "One subject, clearly visible face and body, neutral pose and simple background. No labels or text. Preserve the same identity as the attached image if present; render clothing/coat from the requested caption."
    )
    if layout == "character-sheet" and subject_kind == "animal":
        presentation = "Create a clear animal character reference sheet: front, side, three-quarter and rear full-body views, head close-ups and coat-marking details. All panels show the SAME animal with consistent proportions, ears, muzzle and tail. Preserve identity from the attached image; use the requested coat/accessories. Natural quadruped poses, clear anatomy, neutral background. No human clothing or other animals."
    if subject_kind == "scene":
        full_prompt = f"Create a finished scene appearance reference.\n{prompt}\n{presentation}"
    else:
        full_prompt = (
            f"Create a stable appearance reference for this subject. Identity: {identity_caption}\nOutfit / animal coat: {outfit_caption}\n{prompt}\n"
            + presentation
        )
    request_key = uuid.uuid4().hex
    target.with_suffix(".request.json").write_text(
        json.dumps(
            {
                "model": model,
                "base_url": base,
                "prompt": full_prompt,
                "idempotency_key": request_key,
                "endpoint": base + ("/images/edits" if reference else "/images/generations"),
                "size": size,
                "n": 1,
                "timeout_seconds": timeout,
                "layout": layout,
                "subject_kind": subject_kind,
                "reference_supplied": reference is not None,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    with OpenAI(api_key=key, base_url=base, timeout=timeout, max_retries=0) as client:
        args = {
            "model": model,
            "prompt": full_prompt,
            "size": size,
            "extra_headers": {"Idempotency-Key": request_key},
        }
        args["n"] = 1
        if reference:
            with Path(reference).open("rb") as image:
                response = client.images.edit(image=image, **args)
        else:
            response = client.images.generate(**args)
    datum = response.data[0]
    if getattr(datum, "b64_json", None):
        target.write_bytes(base64.b64decode(datum.b64_json))
    elif getattr(datum, "url", None):
        with urlopen(datum.url, timeout=120) as download:
            target.write_bytes(download.read())
    else:
        raise ValueError("Image model returned neither image bytes nor a URL")
    with Image.open(target) as image:
        suffix = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}.get(image.format)
        image.verify()
    if suffix is None:
        raise ValueError("Image model returned an unsupported image format")
    actual = target.with_suffix(suffix)
    target.rename(actual)
    actual.with_suffix(".json").write_text(
        json.dumps(
            {
                "model": model,
                "prompt": full_prompt,
                "layout": layout,
                "request_id": getattr(response, "_request_id", None),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return (
        actual,
        {"source": "generated", "model": model, "prompt": full_prompt, "layout": layout},
    )
