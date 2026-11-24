"""Generate, inspect, revise and deliver a complete video within the root session."""

from dataclasses import replace
import json
import logging
import shutil
import threading
import uuid
from coda_davinci.media.artifacts import file_hash
from coda_davinci.tools.tool_results import ToolResult
from coda_davinci.workflow.video_inputs import prepare_video_inputs, required_review_times
from coda_davinci.media.video_media import save

LOG = logging.getLogger(__name__)


class VideoWorkflow:
    def __init__(self, tools):
        (self.tools, self.root) = (tools, tools.root)
        self.meta = tools.meta / "video_workflow"
        self.meta.mkdir(exist_ok=True)
        (self.lock, self.active) = (threading.RLock(), set())
        self.round_index = getattr(tools.context, "interaction_round", 0)
        self.practical = tools.settings.review_policy == "practical"
        self.index_path = self.meta / "assets.json"
        self.assets = json.loads(self.index_path.read_text()) if self.index_path.exists() else {}

    def settings(self):
        if self.tools.settings.video_settings is None:
            raise ValueError("Video service configuration is missing")
        return self.tools.settings.video_settings

    def asset(self, path):
        with self.lock:
            value = self.assets.get(path)
            return json.loads(json.dumps(value)) if value else None

    def record(self, path, value):
        with self.lock:
            self.assets[path] = value
            save(self.index_path, self.assets)

    def check_cancelled(self):
        job = self.tools.jobs.current()
        if job:
            job.check_cancelled()
        if self.tools.context:
            self.tools.context.check_cancelled()

    def generate(
        self,
        revision,
        prompt,
        proxy_video,
        reference_videos,
        reference_images,
        shot_manifest_path,
        duration_seconds=None,
    ):
        self.tools.acceptance.criteria()
        if type(revision) is not int or revision < 1:
            raise ValueError("revision must be a positive integer")
        required_skill = (
            "direct-video" if self.tools.settings.generation_route == "direct" else "video-pe"
        )
        if required_skill not in self.tools.skills.loaded:
            raise ValueError(f"Read {required_skill} before calling video_model")
        with self.lock:
            if revision in self.active:
                raise ValueError("This revision is already running; wait for its existing job_id")
            used = {
                a["revision"]
                for a in self.assets.values()
                if a.get("interaction_round", 0) == self.round_index
            }
            from coda_davinci.workflow.video_resume import generation_rejected

            for marker in self.generation_root().glob("v*/.harness/submission_attempt.json"):
                if not generation_rejected(marker.parent.parent):
                    used.add(int(marker.parent.parent.name[1:]))
            cap = self.tools.settings.max_video_revisions
            if cap and revision not in used and (len(used | self.active) >= cap):
                raise ValueError(
                    f"This round allows {cap} content versions. Deliver the best usable version with honest issues; do not keep regenerating."
                )
            self.active.add(revision)
        try:
            return self._generate(
                revision,
                prompt,
                proxy_video,
                reference_videos,
                reference_images,
                shot_manifest_path,
                duration_seconds,
            )
        finally:
            with self.lock:
                self.active.discard(revision)

    def generation_root(self):
        root = self.root / "video_generations"
        return root / f"round_{self.round_index:03d}" if self.round_index else root

    def _generate(
        self,
        revision,
        prompt,
        proxy_video,
        reference_videos,
        reference_images,
        shot_manifest_path,
        duration_seconds,
    ):
        original_refs_path = self.root / ".harness" / "original_reference_images.json"
        original_refs = (
            json.loads(original_refs_path.read_text()) if original_refs_path.is_file() else []
        )
        original_videos_path = self.root / ".harness/reference_videos.json"
        original_videos = (
            json.loads(original_videos_path.read_text()) if original_videos_path.is_file() else []
        )
        if (
            self.tools.settings.generation_route != "direct"
            and original_refs_path.is_file()
            and (not original_refs)
            and (not original_videos)
            and (self.round_index <= 1)
        ):
            index = self.root / ".harness" / "scene_references" / "index.json"
            records = json.loads(index.read_text()) if index.is_file() else []
            scene_paths = {item["path"] for item in records if item.get("viewed")}
            supplied = {
                self.tools.path(item["path"]).relative_to(self.root).as_posix()
                for item in reference_images
            }
            if not scene_paths.intersection(supplied):
                raise ValueError(
                    "T2V requires an inspected scene reference generated by generate_scene_reference before video_model"
                )
        inputs = prepare_video_inputs(
            self.tools,
            prompt,
            proxy_video,
            reference_videos,
            reference_images,
            shot_manifest_path,
            duration_seconds,
        )
        if self.tools.context and proxy_video and (not self.practical):
            self.tools.context.require_review(inputs.manifest["render_id"])
        settings = replace(self.settings(), duration_seconds=inputs.duration, n=1)
        if settings.reference_task_type == "edit" and reference_videos:
            raise ValueError("Edit mode cannot combine original reference videos with the proxy")
        expected = self.tools.settings.video_total_duration
        if (
            expected is not None
            and abs(inputs.duration - expected) > 1e-06
            and (not self.practical)
        ):
            raise ValueError(
                f"Full proxy must preserve the requested {expected}s; received {inputs.duration:g}s"
            )
        directory = self.generation_root() / f"v{revision:04d}"
        directory.mkdir(parents=True, exist_ok=True)
        spec_path = directory / "submission.json"
        previous = json.loads(spec_path.read_text()) if spec_path.exists() else None
        specification = {
            "revision": revision,
            "interaction_round": self.round_index,
            "review_policy": self.tools.settings.review_policy,
            "render_uuid": previous["render_uuid"] if previous else uuid.uuid4().hex,
            "prompt": inputs.prompt,
            "authored_prompt": prompt,
            "shot_manifest": inputs.manifest,
            "duration_seconds": inputs.duration,
            "model": settings.model,
            "base_url": settings.base_url,
            "resolution": settings.resolution,
            "aspect_ratio": settings.aspect_ratio,
            "generate_audio": settings.generate_audio,
            "reference_task_type": settings.reference_task_type,
            "generation_route": self.tools.settings.generation_route,
            "submission_mode": "blender"
            if proxy_video
            else "reference"
            if reference_videos
            else "text",
            "tracked_files": inputs.tracked_files,
            "assembly_revision": self.tools.context.assembly_revision
            if self.tools.context
            else None,
            "references": [
                {
                    "kind": kind,
                    "label": label,
                    "path": source.relative_to(self.root).as_posix(),
                    "sha256": file_hash(source),
                }
                for (kind, label, source) in inputs.sources
            ],
        }
        receipt_path = directory / "receipt.json"
        from coda_davinci.workflow.modeling_policy import enabled, submission_audit

        if enabled(self.tools.settings):
            specification["modeling_policy"] = submission_audit(self.tools, prompt, inputs.prompt)
        if previous:
            if previous != specification:
                raise ValueError(
                    "A revision is immutable; increment revision after changing PE, proxy, references or subjects"
                )
            if receipt_path.exists():
                return json.loads(receipt_path.read_text())
        else:
            save(spec_path, specification)
        save(directory / "shot_manifest.json", inputs.manifest)
        input_dir = directory / "inputs"
        input_dir.mkdir(exist_ok=True)
        (proxy, videos, images) = (None, [], [])
        for index, (kind, label, source) in enumerate(inputs.sources):
            self.check_cancelled()
            target = input_dir / f"{index:03d}{source.suffix.lower()}"
            if not target.exists():
                shutil.copyfile(source, target)
            if file_hash(target) != specification["references"][index]["sha256"]:
                raise ValueError("Reference snapshot changed; create a new revision")
            if kind == "image":
                from PIL import Image

                with Image.open(target) as image:
                    image.verify()
                images.append((label, target))
            else:
                report = self.tools.video_media.check(
                    target, expected_duration=inputs.duration if kind == "proxy" else None
                )
                save(directory / f"input_check_{index:03d}.json", report)
                if report["status"] != "passed":
                    raise ValueError(f"{kind} failed validation before upload: {report['issues']}")
                if kind == "proxy":
                    proxy = target
                else:
                    videos.append(target)
        from coda_davinci.workflow.video_resume import read_record, resume_task_id
        from coda_davinci.providers.video import SeedanceAPIError, run_seedance_generation

        try:
            saved = read_record(directory / ".harness/result.json")
            completed = saved.get("videos", []) if saved.get("status") == "completed" else []
            cached = self.tools.path(completed[0]["path"]) if len(completed) == 1 else None
            if (
                cached is not None
                and cached.is_relative_to(directory / "output")
                and cached.is_file()
                and (cached.stat().st_size > 0)
                and (cached.stat().st_size == completed[0].get("bytes"))
            ):
                generated = saved
                LOG.info(
                    "Resuming downloaded video validation | revision=%s | file=%s", revision, cached
                )
            else:
                generated = run_seedance_generation(
                    settings,
                    self.root,
                    inputs.prompt,
                    task_id=resume_task_id(directory),
                    reference_video=videos + ([proxy] if proxy is not None else []),
                    reference_images=images,
                    proxy_video=proxy,
                    meta_dir=directory / ".harness",
                    output_dir=directory / "output",
                    check_cancelled=self.check_cancelled,
                )
        except SeedanceAPIError as exc:
            if "InputImageSensitiveContentDetected.PrivacyInformation" in str(exc):
                raise SeedanceAPIError(
                    f"{exc}\nNext step: the provider classified an input image as potentially depicting a real person. Inspect the actual submitted images in {directory.relative_to(self.root)}/inputs and their labels in submission.json, including registered identity/outfit references. Follow the PrivacyInformation recovery guidance in video-pe and character-appearance: rework the character reference as a design board with front/side/back views, outfit details and expression panels. Inspect it, update reference bindings and the selected identity anchor, then submit a new revision and inspect the returned video."
                ) from exc
            raise
        outputs = generated.get("videos", [])
        if generated.get("status") != "completed" or len(outputs) != 1:
            raise ValueError(
                f"Video generation incomplete; inspect {directory.relative_to(self.root)}/.harness: {generated.get('message', generated.get('status'))}"
            )
        path = outputs[0]["path"]
        target = self.tools.path(path)
        technical = self.tools.video_media.check(
            target,
            expected_duration=self.tools.settings.video_total_duration or inputs.duration,
            settings=settings,
        )
        save(directory / "technical_check.json", technical)
        asset = {
            "kind": "generated_video",
            "interaction_round": self.round_index,
            "render_uuid": specification["render_uuid"],
            "revision": revision,
            "specification": specification,
            "technical": technical,
            "sha256": file_hash(target),
            "task_id": generated.get("task_id"),
            "review": None,
            "required_times": required_review_times(
                inputs.manifest, technical["duration_seconds"], technical["fps"]
            )
            if "fps" in technical
            else [],
        }
        comparison = None
        if proxy is not None and technical.get("decodable"):
            from coda_davinci.media.video_comparison import create_comparison

            try:
                comparison = create_comparison(
                    self.tools, proxy, target, directory / "comparison", technical
                )
                checkpoint = self.tools.path(proxy_video).parent / "scene.blend"
                if checkpoint.is_file():
                    comparison["scene_checkpoint"] = checkpoint.relative_to(self.root).as_posix()
            except (ValueError, OSError) as exc:
                comparison = {"status": "error", "message": str(exc)}
                LOG.warning("Video comparison failed: %s", exc)
            asset["comparison"] = comparison
        self.record(path, asset)
        receipt = {
            "status": "success" if technical["status"] == "passed" else "error",
            "revision": revision,
            "render_uuid": asset["render_uuid"],
            "video": path,
            "task_id": asset["task_id"],
            "technical_check": technical,
            "comparison": comparison,
            "required_review_timestamps": asset["required_times"],
            "next_step": "Call inspect_video on the returned video and collect its images. When comparison is ready, images show proxy above generated at the same elapsed time. Check motion and event timing, then record_video_review. If it fails, revise Blender/proxy/references/PE or resume responsible children, and submit a new full-video revision before finish.",
        }
        if self.tools.settings.generation_route == "direct":
            receipt["next_step"] = (
                "Inspect the downloaded video and collect its frames, record_video_review, then request_video_acceptance. Revise the action plan, prompt or references when needed."
            )
        save(receipt_path, receipt)
        return receipt

    def require_current_inputs(self, asset):
        spec = asset["specification"]
        if self.tools.context and spec["assembly_revision"] != self.tools.context.assembly_revision:
            raise ValueError(
                "Child assets/camera changed; rebuild and inspect the proxy, then regenerate the video"
            )
        for entry in spec["tracked_files"]:
            path = self.tools.path(entry["path"])
            if not path.is_file() or file_hash(path) != entry["sha256"]:
                raise ValueError(
                    f"Generation inputs changed ({entry['path']}); submit and review a new revision"
                )

    def review(
        self, path, accepted, content, identity_consistency, continuity, prompt_alignment, issues
    ):
        asset = self.asset(path)
        if asset is None or asset.get("interaction_round", 0) != self.round_index:
            raise ValueError("Review must target a video_model result from the current interaction")
        if (
            type(accepted) is not bool
            or not isinstance(issues, list)
            or any((not isinstance(issue, str) or not issue.strip() for issue in issues))
        ):
            raise ValueError("accepted must be boolean; issues must contain nonempty strings")
        observations = dict(
            content=content,
            identity_consistency=identity_consistency,
            continuity=continuity,
            prompt_alignment=prompt_alignment,
        )
        if any(
            (
                not isinstance(value, str) or (not value.strip() and (not self.practical))
                for value in observations.values()
            )
        ):
            raise ValueError(
                "Record content, identity, continuity and prompt agreement with evidence and limitations"
            )
        if file_hash(self.tools.path(path)) != asset["sha256"]:
            raise ValueError("Generated video changed after validation")
        if accepted:
            if asset["technical"]["status"] != "passed":
                raise ValueError("Failed technical checks cannot be accepted")
            if self.practical:
                self.tools.video_media.require_any_view(path, asset["sha256"])
            else:
                self.require_current_inputs(asset)
                self.tools.video_media.require_views(
                    path, asset["sha256"], asset["required_times"], asset["technical"]["fps"]
                )
        asset["review"] = {
            "review_id": uuid.uuid4().hex,
            "accepted": accepted,
            "observations": observations,
            "issues": issues,
            "sha256": asset["sha256"],
        }
        self.record(path, asset)
        save(
            self.meta / "reviews" / f"{asset['review']['review_id']}.json",
            {"video": path, **asset["review"]},
        )
        return {
            "status": "success",
            "video": path,
            "review": asset["review"],
            "next_step": "Finish now with remaining_issues; the three-repair budget is exhausted."
            if self.tools.acceptance.limit_reached()
            else "request_video_acceptance with this result's render_uuid before finish"
            if accepted
            else "Diagnose the cause, revise the responsible files or resume the responsible child; regenerate and inspect.",
        }

    def finish(self, summary, render_uuid, artifacts, remaining_issues):
        with self.lock:
            path = next(
                (p for (p, a) in self.assets.items() if a.get("render_uuid") == render_uuid), None
            )
        asset = self.asset(path) if path else None
        if (
            asset is None
            or asset.get("interaction_round", 0) != self.round_index
            or asset["technical"]["status"] != "passed"
            or (not asset.get("review"))
            or (not asset["review"].get("accepted") and (not self.tools.acceptance.limit_reached()))
        ):
            raise ValueError(
                "finish requires a technically checked and visually accepted video_model result, not a proxy"
            )
        approval = self.tools.acceptance.require(render_uuid)
        if not self.practical:
            self.require_current_inputs(asset)
        warnings = list(asset["technical"].get("warnings", []))
        if self.practical:
            try:
                self.require_current_inputs(asset)
            except ValueError as exc:
                warnings.append(str(exc))
        minor_issues = [
            c["evidence"] for c in approval["verdict"]["checks"] if c["status"] == "minor"
        ]
        if approval.get("delivery_reason") == "review_only":
            minor_issues.extend(
                (
                    c.get("repair") or c["evidence"]
                    for c in approval["verdict"]["checks"]
                    if c["status"] in {"rework", "unverified"}
                )
            )
        if approval.get("delivery_reason") == "rework_limit_reached":
            minor_issues.append(
                "Three-repair limit reached; final delivery bypassed independent acceptance."
            )
        delivery_issues = list(
            dict.fromkeys([*remaining_issues, *asset["review"]["issues"], *warnings, *minor_issues])
        )
        if file_hash(self.tools.path(path)) != asset["sha256"]:
            raise ValueError("Reviewed final video changed; inspect a new generated revision")
        if not artifacts or any((not self.tools.path(p).exists() for p in artifacts)):
            raise ValueError("Final artifacts must exist")
        target = self.root / "Final View"
        if self.round_index:
            target = self.root / "turns" / f"{self.round_index:03d}" / "Final View"
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise FileExistsError("Final View already exists")
        staging = self.meta / f"delivery_{uuid.uuid4().hex}"
        staging.mkdir()
        shutil.copyfile(self.tools.path(path), staging / "video.mp4")
        save(staging / "video_manifest.json", asset)
        save(staging / "acceptance.json", approval)
        staging.rename(target)
        final_dir = target.relative_to(self.root).as_posix()
        result = {
            "status": "completed",
            "interaction_round": self.round_index,
            "summary": summary,
            "render_uuid": render_uuid,
            "final_video": final_dir + "/video.mp4",
            "final_render_dir": final_dir,
            "artifacts": list(
                dict.fromkeys(
                    [*artifacts, final_dir + "/video.mp4", final_dir + "/video_manifest.json"]
                )
            ),
            "remaining_issues": delivery_issues,
            "delivery_quality": "best_effort" if delivery_issues else "accepted",
            "video_render": {
                "status": "completed",
                "revision": asset["revision"],
                "task_id": asset["task_id"],
                "videos": [{"path": final_dir + "/video.mp4"}],
            },
        }
        save(self.meta / "final_delivery.json", result)
        return ToolResult(result, finished=True)
