"""Klink Seedance video-generation client and artifact runner."""

from __future__ import annotations
import json
import logging
import mimetypes
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import Request
from coda_davinci.providers.video_connection import connection_settings, video_opener
from coda_davinci.agents.agent import save_json
from coda_davinci.providers.video_retry import with_retry
from coda_davinci.providers.video_retry import VideoAPIError, api_keys, annotate
from coda_davinci.providers.video_key_retry import run_with_keys

LOG = logging.getLogger(__name__)
SUCCESS_STATUSES = frozenset({"completed", "succeeded", "success", "done"})
FAILURE_STATUSES = frozenset({"failed", "uncertain", "cancelled", "canceled", "expired", "error"})


class SeedanceAPIError(VideoAPIError):
    """A Klink request failed or returned an invalid response."""


@dataclass(frozen=True)
class SeedanceSettings:
    base_url: str
    api_key: str = field(repr=False)
    model: str
    aspect_ratio: str = "16:9"
    resolution: str = "720p"
    duration_seconds: int = 4
    generate_audio: bool = False
    n: int = 1
    request_timeout: int = 300
    generation_timeout: int = 9000
    poll_interval: float = 10.0
    reference_task_type: str = "auto"
    http_proxy: str | None = field(default=None, repr=False)
    https_proxy: str | None = field(default=None, repr=False)
    no_proxy: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        duration = self.duration_seconds
        if (
            isinstance(duration, bool)
            or not isinstance(duration, (int, float))
            or duration <= 0
            or (isinstance(duration, float) and (not duration.is_integer()))
        ):
            raise ValueError("duration_seconds must be a positive whole number of seconds")
        object.__setattr__(self, "duration_seconds", int(duration))
        if self.model == "minimax-h3-max" and (not 5 <= self.duration_seconds <= 15):
            raise ValueError("minimax-h3-max requires 5–15 seconds per generation")
        if self.reference_task_type not in {"auto", "reference", "edit"}:
            raise ValueError("reference_task_type must be auto, reference or edit")

    @classmethod
    def from_env(cls, **overrides) -> "SeedanceSettings":
        values = connection_settings()
        values.update({key: value for (key, value) in overrides.items() if value is not None})
        settings = cls(**values)
        if not settings.base_url:
            raise ValueError("Set VIDEO_MODEL_BASE_URL for Seedance")
        if not settings.api_key:
            raise ValueError("Set VIDEO_MODEL_API_KEY for Seedance")
        if not settings.model:
            raise ValueError("Set VIDEO_MODEL or pass --model")
        if not settings.base_url.startswith(("http://", "https://")):
            raise ValueError("VIDEO_MODEL_BASE_URL must be an http(s) URL")
        if not settings.aspect_ratio.strip() or not settings.resolution.strip():
            raise ValueError("Seedance aspect ratio and resolution must not be empty")
        if settings.duration_seconds <= 0 or settings.n <= 0:
            raise ValueError("Seedance duration and n must be positive")
        if min(settings.request_timeout, settings.generation_timeout, settings.poll_interval) <= 0:
            raise ValueError("Seedance timeouts and poll interval must be positive")
        return settings

    def payload(
        self,
        prompt: str,
        reference_video_url: str | list[str] | None = None,
        reference_image_urls: list[str] | None = None,
    ) -> dict:
        if self.model in ("minimax-h3", "minimax-h3-max") and len(prompt) > 7000:
            raise ValueError(
                f"H3 prompt exceeds 7000 characters: {len(prompt)}. Shorten the authored prompt and verbose subject/shot binding descriptions, preserve required events and reference mappings, then resubmit."
            )
        references = (
            [reference_video_url]
            if isinstance(reference_video_url, str)
            else reference_video_url or []
        )
        if self.reference_task_type == "edit" and len(references) != 1:
            raise ValueError("Edit requests require exactly one proxy video")
        payload = {
            "model": self.model,
            "aspect_ratio": self.aspect_ratio,
            "resolution": self.resolution,
            "duration_seconds": self.duration_seconds,
            "generate_audio": self.generate_audio,
            "n": self.n,
        }
        if not references and (not reference_image_urls):
            payload["prompt"] = prompt
            return payload
        content = [{"type": "text", "text": prompt}]
        for image_url in reference_image_urls or []:
            content.append(
                {"type": "image_url", "image_url": {"url": image_url}, "role": "reference_image"}
            )
        for reference_url in references:
            content.append(
                {
                    "type": "video_url",
                    "video_url": {"url": reference_url},
                    "role": "reference_video",
                }
            )
        payload.update(
            {
                "content": content,
                "output_format": "mp4",
                "omni_reference_task_type": self.reference_task_type,
            }
        )
        if self.reference_task_type == "edit":
            payload.update(duration_seconds=-1, aspect_ratio="adaptive")
        return payload


class SeedanceClient:
    def __init__(self, settings: SeedanceSettings, opener: Callable | None = None):
        self.settings = settings
        self.api_keys = api_keys(settings.api_key)
        self.key_index = 0
        self.api_key = self.api_keys[0] if self.api_keys else ""
        self.upload_cache = {}
        self.generation_payloads = {}
        self.generation_uploads = {}
        self.last_request_id = None
        self.base_url = settings.base_url.rstrip("/") + "/"
        self._open = opener if opener is not None else video_opener(settings)

    def _api_url(self, path: str) -> str:
        return urljoin(self.base_url, path.lstrip("/"))

    def _request(
        self,
        method: str,
        url: str,
        *,
        payload: dict | None = None,
        idempotency_key: str | None = None,
    ) -> tuple[bytes, str | None]:
        return with_retry(
            lambda: self._request_once(
                method, url, payload=payload, idempotency_key=idempotency_key
            ),
            f"Klink {method} {urlsplit(url).path}",
            client=self,
        )

    def _request_once(
        self,
        method: str,
        url: str,
        *,
        payload: dict | None = None,
        idempotency_key: str | None = None,
    ) -> tuple[bytes, str | None]:
        self.last_request_id = None
        headers = {"Accept": "application/json"}
        if urlsplit(url).netloc == urlsplit(self.base_url).netloc:
            headers["Authorization"] = f"Bearer {self.api_key}"
        data = None
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        request = Request(url, data=data, headers=headers, method=method)
        try:
            with self._open(request, timeout=self.settings.request_timeout) as response:
                self.last_request_id = response.headers.get("x-request-id") or response.headers.get(
                    "request-id"
                )
                return (response.read(), response.headers.get("Content-Type"))
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise annotate(
                SeedanceAPIError(
                    f"Klink {method} {urlsplit(url).path} returned HTTP {exc.code}: {body[:2000]}"
                ),
                exc.headers,
                body,
            ) from exc
        except URLError as exc:
            raise SeedanceAPIError(
                f"Klink {method} {urlsplit(url).path} failed: {exc.reason}"
            ) from exc

    def _json_request(
        self,
        method: str,
        path: str,
        *,
        payload: dict | None = None,
        idempotency_key: str | None = None,
    ) -> dict:
        (body, _) = self._request(
            method, self._api_url(path), payload=payload, idempotency_key=idempotency_key
        )
        try:
            value = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SeedanceAPIError("Klink returned a non-JSON response") from exc
        if not isinstance(value, dict):
            raise SeedanceAPIError("Klink returned a JSON value that is not an object")
        return value

    def create_generation(
        self,
        prompt: str,
        idempotency_key: str | None = None,
        reference_video_url: str | list[str] | None = None,
        reference_image_urls: list[str] | None = None,
    ) -> dict:
        if not prompt.strip():
            raise ValueError("Prompt must not be empty")
        idempotency_key = idempotency_key or str(uuid.uuid4())
        if idempotency_key not in self.generation_payloads:
            self.generation_uploads[idempotency_key] = {
                source: uploaded
                for ((index, source), uploaded) in self.upload_cache.items()
                if index == self.key_index
            }
        payload = self.generation_payloads.setdefault(
            idempotency_key,
            self.settings.payload(prompt, reference_video_url, reference_image_urls),
        )
        return self._json_request(
            "POST", "/v1/videos/generations", payload=payload, idempotency_key=idempotency_key
        )

    def get_generation(self, task_id: str) -> dict:
        if not task_id or "/" in task_id:
            raise ValueError("Invalid Seedance task ID")
        return self._json_request("GET", f"/v1/videos/generations/{task_id}")

    def _put_file(self, upload_url: str, source: Path, mime_type: str) -> None:
        with_retry(
            lambda: self._put_file_once(upload_url, source, mime_type),
            "Klink media PUT",
            client=self,
        )

    def _put_file_once(self, upload_url: str, source: Path, mime_type: str) -> None:
        self.last_request_id = None
        parsed = urlsplit(upload_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise SeedanceAPIError("Klink returned an invalid media upload URL")
        size = source.stat().st_size
        headers = {
            "Content-Type": mime_type,
            "Content-Length": str(size),
            "Content-Range": f"bytes 0-{size - 1}/{size}",
        }
        try:
            with source.open("rb") as stream:
                request = Request(upload_url, data=stream, headers=headers, method="PUT")
                with self._open(request, timeout=self.settings.request_timeout) as response:
                    body = response.read()
                    if not 200 <= response.status < 300:
                        error = SeedanceAPIError(
                            f"Klink media upload returned HTTP {response.status}: {body.decode('utf-8', errors='replace')[:2000]}"
                        )
                        error.status_code = response.status
                        raise error
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise annotate(
                SeedanceAPIError(f"Klink media upload returned HTTP {exc.code}: {body[:2000]}"),
                exc.headers,
                body,
            ) from exc
        except OSError as exc:
            raise SeedanceAPIError(f"Klink media upload failed: {exc}") from exc

    def upload_media(self, source: Path, *, expected_type: str) -> dict:
        source = source.resolve()
        cache_key = (self.key_index, source)
        if getattr(self, "_managed_retry", False):
            submitted = self.generation_uploads.get(getattr(self, "idempotency_key", None), {})
            if source in submitted:
                return submitted[source]
            if cache_key in self.upload_cache:
                return self.upload_cache[cache_key]
        if not source.is_file() or source.stat().st_size <= 0:
            raise ValueError(f"Reference media is missing or empty: {source}")
        fallback = "video/mp4" if expected_type == "video" else "image/png"
        mime_type = mimetypes.guess_type(source.name)[0] or fallback
        if not mime_type.startswith(f"{expected_type}/"):
            raise ValueError(f"Reference input is not {expected_type} media: {source}")
        created = self._json_request(
            "POST",
            "/v1/media/uploads",
            payload={
                "filename": source.name,
                "mime_type": mime_type,
                "size_bytes": source.stat().st_size,
            },
        )
        upload_id = created.get("id")
        upload_url = created.get("upload_url")
        if not isinstance(upload_id, str) or not upload_id:
            raise SeedanceAPIError("Klink media upload response is missing an ID")
        if not isinstance(upload_url, str) or not upload_url:
            raise SeedanceAPIError("Klink media upload response is missing upload_url")
        self._put_file(upload_url, source, mime_type)
        completed = self._json_request("POST", f"/v1/media/uploads/{upload_id}/complete")
        if completed.get("status") != "ready":
            raise SeedanceAPIError(
                f"Klink media upload {upload_id} did not become ready: {completed.get('status', 'unknown')}"
            )
        reference_url = completed.get("https_url")
        if not isinstance(reference_url, str) or not reference_url.startswith("https://"):
            raise SeedanceAPIError(
                f"Klink media upload {upload_id} is missing a temporary HTTPS URL"
            )
        completed.setdefault("id", upload_id)
        completed.setdefault("filename", source.name)
        completed.setdefault("mime_type", mime_type)
        completed.setdefault("size_bytes", source.stat().st_size)
        self.upload_cache[cache_key] = completed
        return completed

    def upload_video(self, source: Path) -> dict:
        return self.upload_media(source, expected_type="video")

    def upload_image(self, source: Path) -> dict:
        return self.upload_media(source, expected_type="image")

    def download(self, url: str, target: Path) -> dict:
        return with_retry(
            lambda: self._download_once(url, target), "Klink video download", client=self
        )

    def _download_once(self, url: str, target: Path) -> dict:
        self.last_request_id = None
        headers = {}
        if urlsplit(url).netloc == urlsplit(self.base_url).netloc:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = Request(url, headers=headers, method="GET")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.part")
        try:
            with self._open(request, timeout=self.settings.request_timeout) as response:
                content_type = response.headers.get("Content-Type")
                expected_size = response.headers.get("Content-Length")
                with temporary.open("wb") as output:
                    shutil.copyfileobj(response, output)
                if expected_size is not None and temporary.stat().st_size != int(expected_size):
                    raise SeedanceAPIError(
                        "Incomplete video download: received bytes differ from Content-Length"
                    )
            temporary.replace(target)
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise annotate(
                SeedanceAPIError(f"Klink download returned HTTP {exc.code}: {body[:2000]}"),
                exc.headers,
                body,
            ) from exc
        except URLError as exc:
            raise SeedanceAPIError(f"Klink download failed: {exc.reason}") from exc
        finally:
            if temporary.exists():
                temporary.unlink()
        return {"path": str(target), "bytes": target.stat().st_size, "content_type": content_type}


def generation_status(response: dict) -> str:
    status = response.get("status")
    if not isinstance(status, str) or not status.strip():
        raise SeedanceAPIError("Klink task response is missing a string status")
    return status.lower()


def video_urls(response: dict) -> list[str]:
    """Find output video URLs while excluding thumbnails and task metadata URLs."""
    found: list[str] = []
    negative_tokens = {"cover", "thumbnail", "poster", "preview", "image"}

    def visit(value, path: tuple[str, ...]) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                visit(child, (*path, str(key).lower()))
            return
        if isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, (*path, str(index)))
            return
        if not isinstance(value, str) or not value.startswith(("http://", "https://")):
            return
        key = path[-1] if path else ""
        suffix = Path(urlsplit(value).path).suffix.lower()
        is_video_path = suffix in {".mp4", ".mov", ".webm", ".m4v"}
        is_output_url = key in {"url", "video_url", "download_url", "content_url"}
        if (is_video_path or is_output_url) and (not negative_tokens.intersection(path)):
            if value not in found:
                found.append(value)

    visit(response, ())
    return found


def video_render_prompt(user_prompt: str, prompt_for_dit: str | None = None) -> str:
    prompt = f"Original user request:\n{user_prompt.strip()}"
    if prompt_for_dit is not None:
        prompt += f"\n\nSeedance rendering guidance:\n{prompt_for_dit.strip()}"
    return prompt


def _audit_response(value):
    """Remove temporary signed URLs while preserving useful response metadata."""
    if isinstance(value, dict):
        return {
            key: "[redacted temporary URL]"
            if isinstance(child, str)
            and key.lower().endswith("url")
            and child.startswith(("http://", "https://"))
            else _audit_response(child)
            for (key, child) in value.items()
        }
    if isinstance(value, list):
        return [_audit_response(child) for child in value]
    return value


def _video_suffix(url: str) -> str:
    suffix = Path(urlsplit(url).path).suffix.lower()
    if suffix in {".mp4", ".mov", ".webm", ".m4v"}:
        return suffix
    guessed = mimetypes.guess_extension("video/mp4")
    return guessed or ".mp4"


def _run_seedance_generation_once(
    settings: SeedanceSettings,
    workspace: Path,
    prompt: str,
    *,
    task_id: str | None = None,
    client: SeedanceClient | None = None,
    reference_video: Path | list[Path] | None = None,
    reference_images: list[tuple[str, Path]] | None = None,
    meta_dir: Path | None = None,
    output_dir: Path | None = None,
    check_cancelled: Callable | None = None,
    proxy_video: Path | None = None,
) -> dict:
    """Create/resume one Seedance task, poll it, and download its videos."""
    client = client or SeedanceClient(settings)
    workspace = workspace.resolve()
    meta = (meta_dir or workspace / ".harness").resolve()
    final_dir = (output_dir or workspace / "Final View").resolve()
    if not meta.is_relative_to(workspace) or not final_dir.is_relative_to(workspace):
        raise ValueError("Seedance metadata and output directories must stay in the workspace")
    references = [reference_video] if isinstance(reference_video, Path) else reference_video or []
    resolved_references = [path.resolve() for path in references]
    if any((not path.is_relative_to(workspace) for path in resolved_references)):
        raise ValueError("Seedance reference videos must stay in the workspace")
    resolved_proxy = proxy_video.resolve() if proxy_video is not None else None
    if resolved_proxy is not None and resolved_proxy not in resolved_references:
        raise ValueError("The declared Blender proxy must be one of the attached videos")
    if settings.reference_task_type == "edit" and (
        resolved_proxy is None or len(resolved_references) != 1
    ):
        raise ValueError(
            "Edit mode requires one explicit Blender proxy and no original reference videos"
        )
    submission_mode = (
        "blender" if resolved_proxy else "reference" if resolved_references else "text"
    )
    resolved_reference = resolved_references[0] if resolved_references else None
    resolved_images = [(label, path.resolve()) for (label, path) in reference_images or []]
    if any((not path.is_relative_to(workspace) for (_, path) in resolved_images)):
        raise ValueError("Seedance reference images must stay in the workspace")
    meta.mkdir(parents=True, exist_ok=True)
    idempotency_key = (
        getattr(client, "idempotency_key", None) or str(uuid.uuid4()) if task_id is None else None
    )
    audit_payload = settings.payload(
        prompt,
        [f"[uploaded reference video {i}]" for i in range(1, len(resolved_references) + 1)] or None,
        [f"[uploaded reference image: {label}]" for (label, _) in resolved_images],
    )
    save_json(
        meta / "request.json",
        {
            "provider": "seedance",
            "endpoint": "/v1/videos/generations",
            "payload": audit_payload,
            "reference_video": str(resolved_reference.relative_to(workspace))
            if resolved_reference is not None
            else None,
            "reference_videos": [str(path.relative_to(workspace)) for path in resolved_references],
            "submission_mode": submission_mode,
            "proxy_video": str(resolved_proxy.relative_to(workspace)) if resolved_proxy else None,
            "reference_images": [
                {"label": label, "path": str(path.relative_to(workspace))}
                for (label, path) in resolved_images
            ],
            "resumed_task_id": task_id,
            "idempotency_key": idempotency_key,
            "generation_timeout": settings.generation_timeout,
            "poll_interval": settings.poll_interval,
        },
    )
    final: dict = {"status": "error"}
    phase = "polling" if task_id else "uploading"

    def set_phase(value):
        nonlocal phase
        phase = value
        save_json(meta / "phase.json", {"phase": phase, "task_id": task_id})
        LOG.info("Seedance phase=%s | task=%s | audit=%s", phase, task_id or "pending", meta)

    set_phase(phase)
    try:
        if check_cancelled:
            check_cancelled()
        reference_url = None
        upload = None
        image_uploads = []
        image_urls = []
        if task_id is None:
            for label, path in resolved_images:
                if check_cancelled:
                    check_cancelled()
                image_upload = client.upload_image(path)
                image_uploads.append({**image_upload, "label": label})
                image_urls.append(image_upload["https_url"])
            if image_uploads:
                save_json(meta / "reference_image_uploads.json", _audit_response(image_uploads))
        video_uploads = []
        if task_id is None:
            for path in resolved_references:
                if check_cancelled:
                    check_cancelled()
                video_uploads.append(client.upload_video(path))
                save_json(meta / "reference_video_uploads.json", _audit_response(video_uploads))
            if video_uploads:
                upload = video_uploads[0]
                urls = [item["https_url"] for item in video_uploads]
                reference_url = urls[0] if len(urls) == 1 else urls
                save_json(meta / "reference_upload.json", _audit_response(upload))
        if task_id is None:
            if check_cancelled:
                check_cancelled()
            set_phase("submitting")
            save_json(
                meta / "submission_attempt.json",
                {
                    "provider": "seedance",
                    "idempotency_key": idempotency_key,
                    "mode": submission_mode,
                    "has_proxy_video": resolved_proxy is not None,
                    "reference_image_count": len(resolved_images),
                    "reference_video_count": len(resolved_references),
                },
            )
            response = client.create_generation(
                prompt,
                idempotency_key=idempotency_key,
                reference_video_url=reference_url,
                reference_image_urls=image_urls,
            )
            save_json(meta / "create_response.json", _audit_response(response))
            task_id = response.get("id")
            if not isinstance(task_id, str) or not task_id:
                raise SeedanceAPIError("Klink create response is missing a task ID")
        else:
            response = client.get_generation(task_id)
        set_phase("polling")
        started = time.monotonic()
        poll_number = 0
        while True:
            if check_cancelled:
                check_cancelled()
            poll_number += 1
            save_json(meta / f"poll_{poll_number:04d}.json", _audit_response(response))
            status = generation_status(response)
            LOG.info("Seedance task %s: %s", task_id, status)
            if status in SUCCESS_STATUSES:
                break
            if status in FAILURE_STATUSES:
                message = response.get("error") or response.get("message") or "generation failed"
                error = annotate(
                    SeedanceAPIError(f"Seedance task {task_id} ended as {status}: {message}"),
                    {},
                    str(message),
                )
                error.request_id = error.request_id or getattr(client, "last_request_id", None)
                error.provider_status = status
                raise error
            elapsed = time.monotonic() - started
            if elapsed >= settings.generation_timeout:
                raise TimeoutError(
                    f"Seedance task {task_id} did not finish within {settings.generation_timeout} seconds"
                )
            delay = min(settings.poll_interval, settings.generation_timeout - elapsed)
            if check_cancelled:
                until = time.monotonic() + delay
                while time.monotonic() < until:
                    check_cancelled()
                    time.sleep(min(0.25, max(0, until - time.monotonic())))
            else:
                time.sleep(delay)
            response = client.get_generation(task_id)
        urls = video_urls(response)
        if not urls:
            raise SeedanceAPIError(
                f"Seedance task {task_id} completed without a recognizable video URL"
            )
        set_phase("downloading")
        downloads = []
        for index, url in enumerate(urls, start=1):
            if check_cancelled:
                check_cancelled()
            filename = (
                f"video{_video_suffix(url)}"
                if len(urls) == 1
                else f"video_{index}{_video_suffix(url)}"
            )
            LOG.info(
                "Seedance downloading %d/%d | task=%s | file=%s",
                index,
                len(urls),
                task_id,
                final_dir / filename,
            )
            downloaded = client.download(url, final_dir / filename)
            downloaded["path"] = str((final_dir / filename).relative_to(workspace))
            downloads.append(downloaded)
            LOG.info("Seedance download complete | task=%s | file=%s", task_id, downloaded["path"])
        final = {
            "status": "completed",
            "provider": "seedance",
            "provider_status": status,
            "task_id": task_id,
            "reference_video": str(resolved_reference.relative_to(workspace))
            if resolved_reference is not None
            else None,
            "reference_videos": [str(path.relative_to(workspace)) for path in resolved_references],
            "reference_upload_ids": [item.get("id") for item in video_uploads],
            "reference_upload_id": upload.get("id") if upload is not None else None,
            "reference_images": [
                {
                    "label": item["label"],
                    "path": str(path.relative_to(workspace)),
                    "upload_id": item.get("id"),
                }
                for (item, (_, path)) in zip(image_uploads, resolved_images)
            ],
            "videos": downloads,
        }
        set_phase("completed")
        return final
    except KeyboardInterrupt:
        final = {"status": "interrupted", "provider": "seedance", "task_id": task_id}
        raise
    except Exception as exc:
        final = {
            "status": "error",
            "provider": "seedance",
            "task_id": task_id,
            "type": type(exc).__name__,
            "message": str(exc),
        }
        raise
    finally:
        final["phase"] = phase
        save_json(meta / "result.json", final)


def run_seedance_generation(settings, workspace, prompt, **kwargs):
    return run_with_keys(
        _run_seedance_generation_once, SeedanceClient, settings, workspace, prompt, **kwargs
    )
