import ast
import json
import logging
import os
import shutil
import subprocess
import time
import threading
import uuid
from contextlib import contextmanager, nullcontext
from pathlib import Path
from coda_davinci.runtime.config import Settings, instructions
from coda_davinci.blender.blender_gpu import (
    bootstrap_expression,
    cuda_cache_directory,
    graphics_arguments,
    normalize_engine,
)
from coda_davinci.runtime.sandbox import wrap_command
from coda_davinci.runtime.skill_registry import SkillRegistry, SkillSession
from coda_davinci.tools.tool_jobs import (
    CONTROL_TOOLS,
    ToolInterrupted,
    ToolJobs,
    kill_process_group,
)
from coda_davinci.tools.tool_results import ToolResult, ViewedImage
from coda_davinci.media.video_encoding import collect_frames
from coda_davinci.media.video_preview import sample_clip
from coda_davinci.media.image_transport import image_data_url

LOG = logging.getLogger(__name__)
from coda_davinci.tools.subject_tools import SubjectTools
from coda_davinci.media.subjects import SubjectStore


class WorkspaceTools(SubjectTools):
    def __init__(self, root: Path, settings: Settings, context=None, *, goal: str = ""):
        self.root = root.resolve()
        self.settings = settings
        self.context = context
        subject_workspace = context.team.episode / "scene" if context else self.root
        self.subjects = SubjectStore(subject_workspace / ".harness/subjects")
        resuming = context is not None and context.activation > 1
        self.meta = self.root / ".harness"
        if not resuming and (self.meta / "request.json").exists():
            raise ValueError("Agent workspace already has a conversation; use resume_task")
        self.meta.mkdir(exist_ok=True)
        self.read_only = [self.meta]
        if context is not None:
            for name in ("inputs", "imports"):
                path = self.root / name
                path.mkdir(exist_ok=True)
                self.read_only.append(path)
        child = context is not None and context.is_child
        registry = (
            context.team.skill_registry
            if context
            else SkillRegistry.snapshot(self.meta / "skill_snapshot", settings)
        )
        from coda_davinci.workflow.modeling_policy import freeze

        freeze(self.root, settings)
        self.skills = SkillSession(registry, self.root, resume=resuming)
        self.read_only.append(self.skills.root)
        self.skill_messages = self.skills.activate(
            goal, context.skills if child else settings.skills
        )
        self.schemas = settings.tool_schemas(child=child)
        self.prompt = (
            instructions(settings, child=child)
            + (context.prompt() if context else "")
            + self.skills.prompt()
        )
        self.completion_hint = (
            "Use submit_task with the delivery manifest when done."
            if child
            else "Use finish with the selected render_uuid when done."
        )
        if settings.acceptance_reviewer:
            self.completion_hint = "Inspect the actual candidate and call submit_video_acceptance with every criterion."
        from coda_davinci.media.video_media import VideoMedia
        from coda_davinci.workflow.video_workflow import VideoWorkflow

        self.video_media = VideoMedia(self)
        self.video_workflow = VideoWorkflow(self) if settings.proxy_mode and (not child) else None
        from coda_davinci.agents.acceptance import Acceptance, REVIEW_CANCELLATION

        self.acceptance = Acceptance(self, goal)
        self.review_cancellation = REVIEW_CANCELLATION.get()
        if settings.proxy_mode:
            for name in ("video_generations",):
                directory = self.root / name
                directory.mkdir(exist_ok=True)
                self.read_only.append(directory)
            if settings.interactive and (not child):
                turns = self.root / "turns"
                turns.mkdir(exist_ok=True)
                current_round = getattr(context, "interaction_round", 0)
                self.read_only.extend(
                    (
                        path
                        for path in turns.iterdir()
                        if path.is_dir()
                        and path.name.isdigit()
                        and (int(path.name) < current_round)
                    )
                )
            if not child:
                self.completion_hint = (
                    "Read direct-video, generate and inspect the complete video, then finish after acceptance."
                    if settings.generation_route == "direct"
                    else "Read video-pe, call video_model on the full proxy, inspect the generated video, revise if needed, then finish."
                )
        self.handlers = {tool["name"]: getattr(self, tool["name"]) for tool in self.schemas}
        self.jobs = ToolJobs(self.root, self.meta)
        self.workspace_lock = threading.RLock()
        if context:
            with context.team.lock:
                context.phase = "working"
                context.wakeup = self.jobs.wakeup
                if context.inbox:
                    context.wakeup.set()
        self.process_slots = (
            context.team.process_slots
            if context
            else threading.Semaphore(settings.max_parallel_renders)
        )

    def background_messages(self, *, wait=False):
        messages = self.context.team.messages(self.context) if self.context else []
        messages.extend(self.jobs.messages(wait=wait and (not messages)))
        if self.context:
            messages.extend(self.context.team.messages(self.context))
        return messages

    def _job_result(self, snapshot):
        payload = self.jobs.payload(snapshot["job_id"])
        if payload is None or snapshot["status"] in {"running", "interrupt_requested"}:
            return ToolResult(snapshot)
        if payload.finished:
            self._require_idle()
        if self.context:
            for viewed in payload.images:
                self.context.note_view(viewed.path, self.root)
        self.video_media.note_delivered(payload.images)
        self.note_scene_reference_views(payload.images)
        return ToolResult(snapshot, images=payload.images, finished=payload.finished)

    def wait_tool(self, job_id: str, yield_time_ms: int | None = None):
        return self._job_result(self.jobs.wait(job_id, yield_time_ms))

    def interrupt_tool(self, job_id: str):
        return self.jobs.interrupt(job_id)

    def close(self):
        if self.context:
            self.context.team.cancel_children(self.context)
        self.jobs.close()

    def _require_idle(self):
        if self.jobs.has_active(exclude_current=True):
            raise ValueError(
                "Background tools are still running; use wait_tool or interrupt_tool before completing"
            )
        if self.context and self.context.inbox:
            raise ValueError("Agent messages are pending; read them before completing")

    @contextmanager
    def model_request(self):
        if self.review_cancellation:
            self.review_cancellation()
        with self.context.model_request() if self.context else nullcontext():
            yield
        if self.review_cancellation:
            self.review_cancellation()

    def delegate_tasks(self, tasks: list[dict]):
        if self.context is None:
            raise ValueError("Recursive mode is required")
        job = self.jobs.current()
        return self.context.team.delegate(
            self.context, self.root, tasks, cancel=job.cancelled if job else None
        )

    def resume_task(self, task_id: str, feedback: str, inputs: list[str]):
        if self.context is None:
            raise ValueError("Recursive mode is required")
        job = self.jobs.current()
        return self.context.team.resume(
            self.context,
            self.root,
            task_id,
            feedback,
            inputs,
            cancel=job.cancelled if job else None,
        )

    def send_message(self, task_id: str, message: str):
        return self.context.team.send_message(self.context, task_id, message)

    def list_agents(self):
        return self.context.team.list_agents(self.context)

    def wait_agent(self, task_id: str, yield_time_ms: int | None = None):
        return self.context.team.wait_agent(self.context, task_id, yield_time_ms)

    def interrupt_agent(self, task_id: str):
        return self.context.team.interrupt_agent(self.context, task_id)

    def list_skills(self, query: str = "", offset: int = 0, limit: int = 50):
        return self.skills.list(query, offset, limit)

    def set_acceptance_criteria(self, criteria):
        return self.acceptance.register(criteria)

    def request_video_acceptance(self, render_uuid):
        return self.acceptance.request(render_uuid)

    def submit_video_acceptance(self, checks, summary):
        from coda_davinci.agents.acceptance import submit_verdict

        self._require_idle()
        return submit_verdict(self, checks, summary)

    def read_skill(self, name: str, path: str | None = None):
        return self.skills.read(name, path)

    def submit_task(self, manifest_path: str):
        from coda_davinci.agents.team import submit_task

        self._require_idle()
        if self.context is None:
            raise ValueError("A child task context is required")
        result = submit_task(self.context, self.root, manifest_path)
        return ToolResult(result, finished=True)

    def path(self, value: str) -> Path:
        if not isinstance(value, str) or Path(value).is_absolute():
            raise ValueError("Use a relative workspace path")
        path = (self.root / value).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("Path escapes workspace")
        return path

    def execute(self, name: str, arguments: str) -> ToolResult:
        try:
            if name not in self.handlers:
                raise ValueError(f"Tool is disabled or unknown: {name}")
            values = json.loads(arguments)
            if name not in CONTROL_TOOLS:
                yield_time_ms = values.pop("yield_time_ms", None)
                result = self.jobs.start(
                    name,
                    values,
                    lambda: self._execute_job(name, values),
                    yield_time_ms=yield_time_ms,
                )
                return self._job_result(result)
            else:
                result = self.handlers[name](**values)
            result = result if isinstance(result, ToolResult) else ToolResult(result)
            return result
        except Exception as exc:
            LOG.warning("Tool %s failed: %s", name, exc)
            return ToolResult({"status": "error", "type": type(exc).__name__, "message": str(exc)})

    def _execute_job(self, name, values):
        serial = name in {
            "write_file",
            "edit_file",
            "read_file",
            "list_files",
            "read_skill",
            "list_skills",
        }
        with self.workspace_lock if serial else nullcontext():
            self.jobs.current().check_cancelled()
            return self.handlers[name](**values)

    def write_file(self, path: str, content: str):
        target = self.path(path)
        if any((target.is_relative_to(path) for path in self.read_only)):
            raise ValueError(
                "Execution records, skills, input snapshots and received packages are read-only"
            )
        if target.suffix == ".py":
            ast.parse(content, filename=path)
        if target.exists():
            backup = self.meta / "history" / uuid.uuid4().hex / target.relative_to(self.root)
            backup.parent.mkdir(parents=True)
            backup.write_bytes(target.read_bytes())
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {"status": "success", "path": path}

    def edit_file(self, path: str, old_text: str, new_text: str):
        content = self.path(path).read_text(encoding="utf-8")
        if not old_text or content.count(old_text) != 1:
            raise ValueError("old_text must match exactly one nonempty occurrence")
        return self.write_file(path, content.replace(old_text, new_text, 1))

    def read_file(self, path: str, offset: int, limit: int):
        if offset < 0 or not 1 <= limit <= 40000:
            raise ValueError("Invalid offset or limit")
        target = self.path(path)
        content = target.read_text(encoding="utf-8")
        self.skills.record_access(target, "skill_resource_read", offset=offset, limit=limit)
        return {
            "path": path,
            "content": content[offset : offset + limit],
            "total_characters": len(content),
            "next_offset": min(offset + limit, len(content)),
        }

    def list_files(self, path: str):
        entries = sorted(self.path(path).iterdir())
        return {
            "entries": [
                {
                    "path": str(p.relative_to(self.root)),
                    "directory": p.is_dir(),
                    "bytes": p.stat().st_size,
                }
                for p in entries[:1000]
            ],
            "truncated": len(entries) > 1000,
        }

    def _run(
        self,
        command: list[str],
        *,
        blender: bool = False,
        workspace: Path | None = None,
        hidden_paths: tuple[Path, ...] = (),
    ) -> dict:
        job = self.jobs.current()
        if job:
            job.phase = "queued"
        while not self.process_slots.acquire(timeout=0.25):
            if job:
                job.check_cancelled()
                job.tick()
            if self.context and self.context.is_cancelled():
                raise ToolInterrupted("Task tree cancelled")
        try:
            if job:
                job.check_cancelled()
            if self.context and self.context.is_cancelled():
                raise ToolInterrupted("Task tree cancelled")
            return self._run_process(
                command, blender=blender, workspace=workspace, hidden_paths=hidden_paths
            )
        finally:
            self.process_slots.release()

    def _run_process(
        self,
        command: list[str],
        *,
        blender: bool = False,
        workspace: Path | None = None,
        hidden_paths: tuple[Path, ...] = (),
    ) -> dict:
        execution_root = workspace.resolve() if workspace else self.root
        if hidden_paths and (not self.settings.sandbox):
            raise ValueError("Hiding paths requires sandbox isolation")
        run_dir = self.meta / "runs" / uuid.uuid4().hex
        run_dir.mkdir(parents=True)
        log = run_dir / "process.log"
        (run_dir / "command.json").write_text(json.dumps(command), encoding="utf-8")
        if workspace is not None or hidden_paths:
            (run_dir / "execution.json").write_text(
                json.dumps(
                    {"cwd": str(execution_root), "hidden_paths": [str(p) for p in hidden_paths]},
                    indent=2,
                ),
                encoding="utf-8",
            )
        skill_scripts = [
            Path(command[i + 1]) for (i, value) in enumerate(command[:-1]) if value == "--python"
        ]
        for script in skill_scripts:
            self.skills.record_access(
                script, "skill_script_requested", log=str(log.relative_to(self.root))
            )
        started = time.monotonic()
        timed_out = False
        interrupted = False
        job = self.jobs.current()
        env = {
            k: v
            for (k, v) in os.environ.items()
            if k
            not in {
                "API_KEY",
                "OPENAI_API_KEY",
                "VIDEO_MODEL_API_KEY",
                "GPT_API_KEY",
                "CLAUDE_API_KEY",
                "IMAGE_API_KEY",
            }
        }
        env["PYTHONPATH"] = str(execution_root)
        if hidden_paths:
            env["PYTHONNOUSERSITE"] = "1"
        child_env = {}
        cuda_cache = None
        if blender:
            cuda_cache = cuda_cache_directory()
            library_dir = str(self.settings.blender.parent / "lib")
            inherited_path = env.get("LD_LIBRARY_PATH", "")
            child_env = {
                "LD_LIBRARY_PATH": library_dir + (":" + inherited_path if inherited_path else ""),
                "LD_PRELOAD": None,
                "CUDA_CACHE_PATH": str(cuda_cache),
            }
            (run_dir / "blender_environment.json").write_text(
                json.dumps(child_env, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            LOG.info(
                "Blender library search starts with %s; inherited LD_PRELOAD removed", library_dir
            )
            LOG.info("Blender CUDA cache: %s", cuda_cache)
        if self.settings.sandbox:
            env.update(
                {"HOME": "/tmp/code2video-home", "TMPDIR": "/tmp", "TMP": "/tmp", "TEMP": "/tmp"}
            )
            command = wrap_command(
                command,
                executable=self.settings.bwrap,
                workspace=execution_root,
                cwd=execution_root,
                read_only=tuple((p for p in self.read_only if p.is_relative_to(execution_root))),
                env_overrides=child_env,
                hidden_paths=hidden_paths,
                cuda_cache=cuda_cache,
            )
        else:
            for name, value in child_env.items():
                if value is None:
                    env.pop(name, None)
                else:
                    env[name] = value
        (run_dir / "launch_command.json").write_text(json.dumps(command), encoding="utf-8")
        if job:
            job.check_cancelled()
        with log.open("w") as stream:
            process = subprocess.Popen(
                command,
                cwd=execution_root,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=stream,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            try:
                if job:
                    job.attach(
                        process,
                        str(log.relative_to(self.root)),
                        "blender" if blender else "encoding",
                    )
                (run_dir / "process.json").write_text(
                    json.dumps(
                        {
                            "pid": process.pid,
                            "pgid": process.pid,
                            "job_id": job.job_id if job else None,
                            "tool_timeout": self.settings.tool_timeout,
                        },
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                next_log = time.monotonic() + 5
                while (
                    os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is None
                ):
                    if (
                        job
                        and job.cancelled.is_set()
                        or (self.context and self.context.is_cancelled())
                    ):
                        interrupted = True
                        break
                    if job:
                        job.tick()
                    remaining = self.settings.tool_timeout - (time.monotonic() - started)
                    if remaining <= 0:
                        timed_out = True
                        break
                    cancel_event = job.cancelled if job else self.jobs.stopping
                    cancel_event.wait(timeout=min(0.25, remaining))
                    if time.monotonic() >= next_log:
                        LOG.info(
                            "Process %s running (%.0fs); log=%s",
                            process.pid,
                            time.monotonic() - started,
                            log,
                        )
                        next_log = time.monotonic() + 5
            finally:
                with self.jobs.changed:
                    kill_process_group(process)
                    try:
                        process.wait(timeout=5)
                    finally:
                        if job:
                            job.detach(process)
        interrupted = interrupted or bool(job and job.cancelled.is_set())
        with log.open("rb") as stream:
            stream.seek(max(0, log.stat().st_size - 12000))
            tail = stream.read().decode("utf-8", errors="replace")
        result = {
            "status": "interrupted"
            if interrupted
            else "success"
            if process.returncode == 0 and (not timed_out)
            else "error",
            "pid": process.pid,
            "pgid": process.pid,
            "exit_code": process.returncode,
            "timed_out": timed_out,
            "interrupted": interrupted,
            "elapsed_seconds": round(time.monotonic() - started, 2),
            "log": str(log.relative_to(self.root)),
            "log_tail": tail,
        }
        for script in skill_scripts:
            self.skills.record_access(
                script, "skill_script_process_finished", status=result["status"], log=result["log"]
            )
        return result

    def _blender_base_command(self, engine: str | None = None):
        command = [
            str(self.settings.blender),
            "--background",
            "--factory-startup",
            "--disable-autoexec",
        ]
        command.extend(graphics_arguments(normalize_engine(engine or self.settings.engine)))
        command.extend(["--python-exit-code", "1"])
        return command

    def _blender_command(
        self,
        scripts: list[str],
        blend_file: str | None,
        threads: int | None = None,
        engine: str | None = None,
    ):
        if not scripts:
            raise ValueError("Supply at least one script")
        if threads is not None and (type(threads) is not int or not 0 <= threads <= 1024):
            raise ValueError("threads must be an integer from 0 to 1024, or null")
        selected_engine = normalize_engine(engine or self.settings.engine)
        command = self._blender_base_command(selected_engine)
        if blend_file is not None:
            blend = self.path(blend_file)
            if not blend.is_file():
                raise FileNotFoundError(blend_file)
            command.append(str(blend))
        if threads is not None:
            command.extend(["--threads", str(threads)])
        command.extend(
            [
                "--python-expr",
                f"import bpy; bpy.context.scene.render.engine = {selected_engine!r}",
                "--python-expr",
                bootstrap_expression(),
                "--python-expr",
                f"import sys; sys.path.insert(0, {str(self.root)!r})",
            ]
        )
        for script in scripts:
            path = self.path(script)
            if not path.is_file() or path.suffix != ".py":
                raise ValueError(f"Not a Python script: {script}")
            tracer = (Path(__file__).parents[1] / "blender/scene_provenance.py").resolve()
            command.extend(
                [
                    "--python-expr",
                    f"import runpy, builtins; builtins._c2v_trace = runpy.run_path({str(tracer)!r}); builtins._c2v_record = builtins._c2v_trace['begin']({script!r}, {str(self.root)!r})",
                    "--python",
                    str(path),
                    "--python-expr",
                    "import builtins; builtins._c2v_trace['end'](builtins._c2v_record)",
                ]
            )
        return command

    def inspect_scene_at(self, blend_file, time_seconds, x, y):
        from coda_davinci.tools.scene_tools import inspect_scene_at

        return inspect_scene_at(self, blend_file, time_seconds, x, y)

    def preview_camera_variants(
        self, blend_file, variants, timestamps_seconds, clip_start_seconds, clip_duration_seconds
    ):
        from coda_davinci.tools.scene_tools import preview_camera_variants

        return preview_camera_variants(
            self,
            blend_file,
            variants,
            timestamps_seconds,
            clip_start_seconds,
            clip_duration_seconds,
        )

    def apply_camera_variant(self, preview_id, variant_name, output_blend):
        from coda_davinci.tools.scene_tools import apply_camera_variant

        return apply_camera_variant(self, preview_id, variant_name, output_blend)

    def run_blender(
        self,
        scripts: list[str],
        blend_file: str | None,
        threads: int | None = None,
        engine: str | None = None,
    ):
        return self._run(self._blender_command(scripts, blend_file, threads, engine), blender=True)

    def render_image(
        self,
        scripts: list[str],
        blend_file: str | None,
        threads: int | None = None,
        engine: str | None = None,
    ):
        """Render the current scene frame to one PNG without creating a video."""
        revision = self.context.assembly_revision if self.context else None
        command = self._blender_command(scripts, blend_file, threads, engine)
        output = self.root / "renders" / uuid.uuid4().hex
        output.mkdir(parents=True)
        worker = (Path(__file__).parents[1] / "blender/image_worker.py").resolve()
        command.extend(["--python", str(worker), "--", str(output)])
        LOG.info("Rendering still image: %s", output)
        rendered = self._run(command, blender=True)
        paths = {
            "render_uuid": output.name,
            "image": str((output / "image.png").relative_to(self.root)),
        }
        if rendered["status"] != "success":
            return {**rendered, **paths, "stage": "render"}
        if not (output / "image.png").is_file():
            return {
                "status": "error",
                "stage": "render",
                **paths,
                "message": "Blender exited successfully but image.png is missing",
                "render": rendered,
            }
        if self.context:
            self.context.note_render(output.name, revision)
        return {"status": "success", **paths, "render": rendered}

    def render_sequence(
        self,
        scripts: list[str],
        blend_file: str | None,
        threads: int | None = None,
        engine: str | None = None,
    ):
        revision = self.context.assembly_revision if self.context else None
        command = self._blender_command(scripts, blend_file, threads, engine)
        output = self.root / "renders" / uuid.uuid4().hex
        output.mkdir(parents=True)
        worker = (Path(__file__).parents[1] / "blender/sequence_worker.py").resolve()
        command.extend(["--python", str(worker), "--", str(output)])
        LOG.info("Rendering animation sequence: %s", output)
        rendered = self._run(command, blender=True)
        paths = {
            "render_uuid": output.name,
            "frames": str((output / "frames").relative_to(self.root)),
        }
        if rendered["status"] != "success":
            return {**rendered, **paths, "stage": "render"}
        manifest = json.loads((output / "sequence.json").read_text())
        if self.context:
            self.context.note_render(output.name, revision)
        return {
            "status": "success",
            "stage": "render",
            **paths,
            "render": rendered,
            "sequence": str((output / "sequence.json").relative_to(self.root)),
            "scene_checkpoint": str((output / "scene.blend").relative_to(self.root)),
            "fps": manifest["fps"],
            "frame_count": len(manifest["frames"]),
        }

    def encode_video(self, frame_dirs: list[str], fps: float | None = None):
        """Snapshot ordered segments into one self-contained, reviewable video package."""
        revision = self.context.assembly_revision if self.context else None
        (frames, fps, segments) = collect_frames(self.root, frame_dirs, fps)
        if self.context:
            for frame in frames:
                parts = frame.relative_to(self.root).parts
                if (
                    len(parts) >= 3
                    and parts[0] == "renders"
                    and (self.context.render_revisions.get(parts[1], revision) != revision)
                ):
                    revision = -1
                    break
        output = self.root / "renders" / uuid.uuid4().hex
        (output / "frames").mkdir(parents=True)
        manifest = {"fps": fps, "frames": [], "segments": segments, "source_frames": []}
        job = self.jobs.current()
        for index, source in enumerate(frames, 1):
            if job:
                job.check_cancelled()
            if self.context:
                self.context.check_cancelled()
            relative = f"frames/frame_{index:08d}.png"
            shutil.copyfile(source, output / relative)
            manifest["frames"].append(relative)
            manifest["source_frames"].append(source.relative_to(self.root).as_posix())
        (output / "sequence.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        paths = {
            "render_uuid": output.name,
            "frames": str((output / "frames").relative_to(self.root)),
            "sequence": str((output / "sequence.json").relative_to(self.root)),
            "fps": fps,
            "frame_count": len(frames),
            "duration_seconds": len(frames) / fps,
        }
        try:
            encoded = self._encode_sequence(output, manifest)
        except (OSError, ValueError) as exc:
            return {"status": "error", "stage": "encode", **paths, "message": str(exc)}
        if encoded["status"] == "success" and (not (output / "video.mp4").is_file()):
            encoded = {
                **encoded,
                "status": "error",
                "message": "FFmpeg exited successfully but video.mp4 is missing",
            }
        if self.context:
            self.context.note_render(output.name, revision)
        return {
            "status": encoded["status"],
            "stage": "encode",
            **paths,
            "video": str((output / "video.mp4").relative_to(self.root))
            if encoded["status"] == "success"
            else None,
            "encoding": encoded,
        }

    def view_image(self, path: str):
        image = self.path(path)
        if not image.is_file() or image.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            raise ValueError("Expected an existing PNG/JPEG/WebP image")
        self.skills.record_access(image, "skill_resource_viewed")
        return ToolResult(
            {"status": "success", "path": path}, images=[ViewedImage(image, f"Image: {path}")]
        )

    def _external_video(self, path):
        from coda_davinci.media.external_video import VideoReader

        return VideoReader(self.path(path), self.root, self.settings, self._run)

    def inspect_video(self, path: str, timestamps_seconds: list):
        return self.video_media.inspect(path, timestamps_seconds)

    def video_model(
        self,
        revision,
        prompt,
        proxy_video,
        reference_videos,
        reference_images,
        shot_manifest_path,
        duration_seconds=None,
    ):
        return self.video_workflow.generate(
            revision,
            prompt,
            proxy_video,
            reference_videos,
            reference_images,
            shot_manifest_path,
            duration_seconds,
        )

    def video_tail_frame(self, path):
        reader = self._external_video(path)
        return self._view_decoded_frames(path, reader, [reader.info["total_frames"] - 1])

    def record_video_review(
        self, path, accepted, content, identity_consistency, continuity, prompt_alignment, issues
    ):
        return self.video_workflow.review(
            path, accepted, content, identity_consistency, continuity, prompt_alignment, issues
        )

    def video_info(self, path: str):
        video = self.path(path)
        if video.suffix.lower() == ".json" or (video.parent / "sequence.json").is_file():
            (_, manifest) = self._load_sequence(path)
            from coda_davinci.media.video_encoding import frame_rate

            fps = frame_rate(manifest["fps"])
            return {
                "status": "success",
                "source": "rendered_frames",
                "video": path,
                "total_frames": len(manifest["frames"]),
                "source_fps": fps,
                "duration_seconds": len(manifest["frames"]) / fps,
            }
        return {
            "status": "success",
            "source": "decoded_video",
            "video": path,
            **self._external_video(path).metadata(),
        }

    def _view_decoded_frames(self, path, reader, indices):
        from coda_davinci.media.artifacts import file_hash

        digest = file_hash(reader.path)
        frames = reader.frames(indices)
        missing = [i for i in indices if i not in frames]
        rows = [
            {
                "frame_index": i,
                "timestamp_seconds": reader.info["timestamps"][i],
                "image": frames[i].relative_to(self.root).as_posix(),
            }
            for i in indices
            if i in frames
        ]
        if file_hash(reader.path) != digest:
            raise ValueError("Video changed during inspection; inspect the new version")
        with self.video_media.lock:
            for row in rows:
                self.video_media.preview_sources.setdefault(
                    str(self.root / row["image"]), set()
                ).add((path, digest, row["timestamp_seconds"]))
        return ToolResult(
            {
                "status": "partial" if missing and rows else "error" if missing else "success",
                "source": "decoded_video",
                "video": path,
                **reader.metadata(),
                "requested_frame_indices": indices,
                "missing_frame_indices": missing,
                "frames": rows,
            },
            images=[
                ViewedImage(
                    self.root / row["image"],
                    f"Video {path}; time={row['timestamp_seconds']:.6f}s; frame_index={row['frame_index']}",
                )
                for row in rows
            ],
        )

    def view_video_frame(self, path: str, frame_indices: list[int]):
        if (
            not isinstance(frame_indices, list)
            or not frame_indices
            or any(
                (
                    isinstance(index, bool) or not isinstance(index, int) or index < 0
                    for index in frame_indices
                )
            )
        ):
            raise ValueError("frame_indices must be a nonempty list of nonnegative integers")
        video = self.path(path)
        if video.suffix.lower() != ".json" and (not (video.parent / "sequence.json").is_file()):
            return self._view_decoded_frames(path, self._external_video(path), frame_indices)
        (video, manifest) = self._load_sequence(path)
        return self._view_sequence_frames(path, video, manifest["frames"], frame_indices)

    def view_video_clip(self, path: str, start_seconds: float, duration_seconds: float, fps: float):
        video = self.path(path)
        if video.suffix.lower() != ".json" and (not (video.parent / "sequence.json").is_file()):
            reader = self._external_video(path)
            preview = reader.samples(start_seconds, duration_seconds, fps)
            samples = preview.pop("samples")
            result = self._view_decoded_frames(path, reader, [s["frame_index"] for s in samples])
            for row, sample in zip(result.data["frames"], samples):
                row.update(sample)
            result.data.update(
                **preview,
                requested_frame_count=len(samples),
                returned_frame_count=len(result.images),
            )
            return result
        (video, manifest) = self._load_sequence(path)
        preview = sample_clip(
            len(manifest["frames"]), manifest.get("fps"), start_seconds, duration_seconds, fps
        )
        samples = preview.pop("samples")
        result = self._view_sequence_frames(
            path, video, manifest["frames"], [sample["frame_index"] for sample in samples]
        )
        result.data.update(
            **preview, requested_frame_count=len(samples), returned_frame_count=len(result.images)
        )
        by_index = {sample["frame_index"]: sample for sample in samples}
        for row, image in zip(result.data["frames"], result.images):
            row.update(by_index[row["frame_index"]])
            image.label = f"Preview of {path}; time={row['timestamp_seconds']:.6f}s; sample_time={row['sample_time_seconds']:.6f}s; frame_index={row['frame_index']} (zero-based)"
        return result

    def _load_sequence(self, path):
        video = self.path(path)
        manifest_path = video.parent / "sequence.json"
        if not manifest_path.is_file():
            raise ValueError(
                "sequence.json is required beside the video path; video decoding is not used"
            )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.skills.record_access(manifest_path, "skill_resource_read")
        if (
            not isinstance(manifest, dict)
            or not isinstance(manifest.get("frames"), list)
            or any((not isinstance(frame, str) for frame in manifest["frames"]))
        ):
            raise ValueError("sequence.json frames must be a list of paths")
        return (video, manifest)

    def _view_sequence_frames(self, path, video, frames, frame_indices):
        extracted = {}
        for index in set(frame_indices):
            if index < len(frames):
                frame = self.path(str(video.parent.relative_to(self.root) / frames[index]))
                if frame.is_file():
                    extracted[index] = frame
        missing = [index for index in frame_indices if index not in extracted]
        images = [
            ViewedImage(
                extracted[index], f"Source render for {path}; frame_index: {index} (zero-based)"
            )
            for index in frame_indices
            if index in extracted
        ]
        for viewed in images:
            self.skills.record_access(viewed.path, "skill_resource_viewed")
        return ToolResult(
            {
                "status": "partial" if missing and images else "error" if missing else "success",
                "source": "rendered_frames",
                "total_frames": len(frames),
                "video": path,
                "requested_frame_indices": frame_indices,
                "frames": [
                    {"frame_index": index, "image": str(extracted[index].relative_to(self.root))}
                    for index in frame_indices
                    if index in extracted
                ],
                "missing_frame_indices": missing,
            },
            images=images,
        )

    def _encode_sequence(self, output: Path, manifest: dict):
        return self._run(
            [
                self.settings.ffmpeg,
                "-nostdin",
                "-n",
                "-framerate",
                str(manifest["fps"]),
                "-start_number",
                "1",
                "-i",
                str(output / "frames" / "frame_%08d.png"),
                "-frames:v",
                str(len(manifest["frames"])),
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-vf",
                "pad=ceil(iw/2)*2:ceil(ih/2)*2",
                "-movflags",
                "+faststart",
                str(output / "video.mp4"),
            ]
        )

    def finish(
        self,
        summary: str,
        render_uuid: str,
        artifacts: list[str],
        remaining_issues: list[str],
        shot_manifest_path: str | None = None,
    ):
        self._require_idle()
        if self.video_workflow:
            return self.video_workflow.finish(summary, render_uuid, artifacts, remaining_issues)
        if self.context:
            self.context.require_review(render_uuid)
        if uuid.UUID(render_uuid).hex != render_uuid:
            raise ValueError(
                "render_uuid must be the 32-character ID returned by render_image or encode_video"
            )
        if not artifacts or any((not self.path(p).exists() for p in artifacts)):
            raise ValueError("Final artifacts must exist in the workspace")
        source = self.path(f"renders/{render_uuid}")
        final_dir = "Final View"
        target = self.path(final_dir)
        is_sequence = (source / "video.mp4").is_file() and (source / "frames").is_dir()
        if is_sequence:
            self.acceptance.require(render_uuid)
        is_image = (source / "image.png").is_file()
        if not is_sequence and (not is_image):
            raise ValueError("Selected render must contain image.png or video.mp4 and frames/")
        if self.settings.proxy_mode and (not is_sequence or not shot_manifest_path):
            raise ValueError(
                "PE mode requires a final video and shot_manifest_path authored from that video"
            )
        manifest = None
        if shot_manifest_path is not None:
            if not is_sequence:
                raise ValueError("shot_manifest_path is only valid for a video delivery")
            from coda_davinci.workflow.references import load_shot_manifest, proxy_observations

            manifest = load_shot_manifest(
                self.path(shot_manifest_path),
                expected_render_id=render_uuid,
                reference_labels=set(),
            )
            from coda_davinci.media.subjects import bind_manifest

            bind_manifest(self.subjects, manifest, [])
            proxy_observations(source / "video.mp4", manifest)
        if target.exists():
            raise FileExistsError(f"Final destination already exists: {final_dir}")
        if any((p.is_symlink() for p in source.rglob("*"))):
            raise ValueError("Selected render directory must not contain symlinks")
        staging = self.meta / f"submission_{uuid.uuid4().hex}"
        LOG.info("Copying final render %s to %s", render_uuid, final_dir)
        shutil.copytree(source, staging)
        if manifest is not None:
            (staging / "shot_manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        staging.rename(target)
        return ToolResult(
            {
                "status": "completed",
                "summary": summary,
                "artifacts": artifacts,
                "render_uuid": render_uuid,
                "final_render_dir": final_dir,
                "final_frames": f"{final_dir}/frames" if is_sequence else None,
                "final_video": f"{final_dir}/video.mp4" if is_sequence else None,
                "final_image": f"{final_dir}/image.png" if is_image else None,
                "shot_manifest_path": f"{final_dir}/shot_manifest.json"
                if manifest is not None
                else None,
                "remaining_issues": remaining_issues,
            },
            finished=True,
        )


def image_input(path: Path) -> dict:
    return {"type": "input_image", "detail": "high", "image_url": image_data_url(path)}
