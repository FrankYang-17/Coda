from coda_davinci.runtime.prompts import load_prompt

"Recursive fork/join tasks with isolated workspaces and shared concurrency limits."
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
import json
import logging
from pathlib import Path
import shutil
import threading
import time
import uuid
from coda_davinci.media.artifacts import KINDS, copy_files, local_path, read_delivery, text_list
from coda_davinci.runtime.skill_registry import SkillRegistry
from coda_davinci.tools.tool_jobs import WAIT_YIELD_MS, wait_seconds


def _revision_target(base: Path, activation: int) -> Path:
    target = base / "revisions" / f"{activation:04d}"
    if target.exists() or (target.parent.exists() and (not target.parent.is_dir())):
        target = base / f"revisions_{uuid.uuid4().hex}" / f"{activation:04d}"
    return target


@dataclass
class TaskContext:
    team: "Team"
    task_id: str
    parent_id: str | None
    depth: int
    kind: str
    used: int = 0
    status: str = "running"
    assembly_revision: int = 0
    render_revisions: dict[str, int] = field(default_factory=dict)
    viewed_renders: dict[str, int] = field(default_factory=dict)
    skills: tuple[str, ...] = ()
    activation: int = 1
    interaction_round: int = 0
    state: str = "running"
    inbox: list[dict] = field(default_factory=list)
    wakeup: threading.Event = field(default_factory=threading.Event, repr=False)
    stopped: threading.Event = field(default_factory=threading.Event, repr=False)
    parent_cancel: threading.Event | None = field(default=None, repr=False)
    result: dict | None = None
    phase: str = "preparing"

    def is_cancelled(self):
        if self.team.cancelled.is_set() or self.stopped.is_set():
            return True
        if self.parent_cancel is not None and self.parent_cancel.is_set():
            return True
        parent = self.team.tasks.get(self.parent_id)
        return parent.is_cancelled() if parent else False

    def check_cancelled(self):
        if self.is_cancelled():
            raise RuntimeError("Agent activation interrupted")

    @property
    def is_child(self):
        return self.parent_id is not None

    @contextmanager
    def model_request(self):
        with self.team.slot(self.team.model_slots, self):
            self.check_cancelled()
            with self.team.lock:
                self.used += 1
            yield

    def note_render(self, render_id: str, revision: int | None = None):
        self.render_revisions[render_id] = self.assembly_revision if revision is None else revision

    def note_view(self, path: Path, workspace: Path):
        parts = path.relative_to(workspace).parts
        if len(parts) < 3 or parts[0] != "renders":
            return
        render_id = parts[1]
        revision = self.render_revisions.get(render_id)
        if revision is not None:
            self.viewed_renders[render_id] = revision

    def require_review(self, render_id: str | None = None):
        revisions = (
            [self.viewed_renders.get(render_id)]
            if render_id is not None
            else self.viewed_renders.values()
        )
        if self.assembly_revision not in revisions:
            raise ValueError(
                "Render and view the current assembled scene after receiving child assets"
            )

    def prompt(self) -> str:
        if not self.team.settings.recursive and (not self.is_child):
            return f"Persistent case session, interaction round {self.interaction_round}. Reuse the existing scene and memory."
        role = (
            'You are the dedicated camera and shot-planning agent for this task. Use the\nshowcase-camera skill (load it if it is not already in your context). Own the camera\nsetup, framing, shot timing, movement and cuts for the assigned video scope; preserve\nthe parent\'s scene geometry and honor requested fixed-camera shots. Deliver a portable\ncamera setup script, a complete shots.json and inspected previews with kind="camera".\nFor requested video output, default to 24 FPS unless another frame rate is explicitly\nrequested. For a new timeline use scene.render.fps=24, scene.render.fps_base=1.0 and\nscene.frame_step=1. Derive frame counts, shot ranges and keyframes from the requested\nduration at that rate (20 seconds means 480 frames). render_sequence records effective\nFPS divided by frame_step; encode_video with fps=null inherits it. Do not lower timeline\nFPS and increase frame_step for the same reduction.\nRead showcase-camera for how to sample an existing higher-rate timeline without changing duration.\nComplete the assigned camera scope locally; preview videos do not require further delegation.\n'
            if self.kind == "camera"
            else ""
        )
        completion = (
            "Use submit_task with a delivery/manifest.json to complete this subtask; finish is unavailable. Read delivery-contract before preparing the package."
            if self.is_child
            else "Use finish to submit the final reviewed scene render."
        )
        if self.team.settings.proxy_mode:
            if not self.is_child:
                completion = "Use finish only for the inspected and accepted full video_model result, not a Blender proxy. Keep this session open for revisions."
            if self.kind == "camera":
                role += "\nDeliver full-proxy camera code, shots and previews. After generated-video review, the parent may resume you with concrete camera or timing corrections."
        return load_prompt(
            "delegation_1.md",
            self_task_id=self.task_id,
            self_kind=self.kind,
            self_depth=self.depth,
            self_activation=self.activation,
            self_team_settings_max_depth=self.team.settings.max_depth,
            completion=completion,
            role=role,
            self_team_settings_max_parallel_agents=self.team.settings.max_parallel_agents,
            self_parent_id=repr(self.parent_id),
        )


class Team:
    def __init__(self, settings, episode: Path, runner):
        self.settings = settings
        self.episode = episode
        self.runner = runner
        self.lock = threading.Lock()
        self.cancelled = threading.Event()
        self.model_slots = threading.Semaphore(settings.max_parallel_agents)
        self.process_slots = threading.Semaphore(settings.max_parallel_renders)
        self.skill_registry = SkillRegistry.snapshot(episode / "skill_snapshot", settings)
        self.root = TaskContext(self, "root", None, 0, "scene")
        self.tasks = {"root": self.root}
        (episode / "agents").mkdir()

    def _enqueue(self, recipient, event):
        """Caller holds the team lock. Wake waits without acquiring the job-manager lock."""
        recipient.inbox.append(event)
        recipient.wakeup.set()
        try:
            with (self.episode / "agent_messages.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(
                    json.dumps({"recipient": recipient.task_id, **event}, ensure_ascii=False) + "\n"
                )
        except OSError:
            logging.exception("Could not persist agent message; it remains queued in memory")

    def messages(self, context):
        with self.lock:
            (events, context.inbox) = (context.inbox, [])
            context.wakeup.clear()
        return [
            {
                "role": "user",
                "content": "Agent communication:\n" + json.dumps(event, ensure_ascii=False),
            }
            for event in events
        ]

    def _direct_child(self, parent, task_id):
        child = self.tasks.get(task_id)
        if child is None or child.parent_id != parent.task_id:
            raise ValueError("Only a direct child of this agent can be controlled")
        return child

    def send_message(self, sender, task_id, message):
        if not isinstance(message, str) or not message.strip():
            raise ValueError("message must be nonempty text")
        with self.lock:
            recipient = self.tasks.get(task_id)
            if recipient is None or not (
                recipient.parent_id == sender.task_id or sender.parent_id == task_id
            ):
                raise ValueError("Messages may target your parent or direct children only")
            event = {
                "type": "agent_message",
                "message_id": uuid.uuid4().hex,
                "sender": sender.task_id,
                "message": message,
                "created_at": time.time(),
            }
            self._enqueue(recipient, event)
            return {
                "status": "queued",
                "message_id": event["message_id"],
                "task_id": task_id,
                "state": recipient.state,
                "requires_resume": recipient.state == "idle",
            }

    def _snapshot(self, context):
        return {
            "task_id": context.task_id,
            "parent_id": context.parent_id,
            "kind": context.kind,
            "state": context.state,
            "status": context.status,
            "phase": context.phase,
            "activation": context.activation,
            "model_requests": context.used,
            "interrupt_requested": context.is_cancelled(),
            "result": context.result,
        }

    def list_agents(self, caller):
        with self.lock:
            return {
                "status": "success",
                "child_slots": self._child_slots(caller),
                "agents": [
                    self._snapshot(c)
                    for c in self.tasks.values()
                    if c.task_id == caller.task_id
                    or c.task_id == caller.parent_id
                    or c.parent_id == caller.task_id
                ],
            }

    def _child_slots(self, parent):
        """Caller holds self.lock; reservations use the same state as active execution."""
        active = sum(
            (c.parent_id == parent.task_id and c.state == "running" for c in self.tasks.values())
        )
        limit = self.settings.max_parallel_agents
        return {"limit": limit, "active": active, "available": max(0, limit - active)}

    def _admission_error(self, parent, requested):
        slots = self._child_slots(parent)
        if requested > slots["available"]:
            return {
                "status": "error",
                "code": "agent_concurrency_limit",
                "parent_id": parent.task_id,
                "requested": requested,
                **slots,
                "message": "Not enough active-child slots. No agents were started or queued; submit a smaller batch or wait for a child to become idle before retrying.",
            }
        return None

    def wait_agent(self, parent, task_id, yield_time_ms=None):
        deadline = time.monotonic() + wait_seconds(yield_time_ms, WAIT_YIELD_MS)
        while True:
            with self.lock:
                child = self._direct_child(parent, task_id)
                snapshot = self._snapshot(child)
                if child.state == "idle" or parent.inbox or parent.is_cancelled():
                    return snapshot
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return snapshot
            parent.wakeup.wait(min(0.25, remaining))

    def cancel_children(self, parent):
        with self.lock:
            pending = [parent.task_id]
            while pending:
                task_id = pending.pop()
                for child in self.tasks.values():
                    if child.parent_id == task_id and child.state == "running":
                        child.stopped.set()
                        child.wakeup.set()
                        pending.append(child.task_id)

    def interrupt_agent(self, parent, task_id):
        with self.lock:
            child = self._direct_child(parent, task_id)
            if child.state == "running":
                child.stopped.set()
                child.wakeup.set()
            return self._snapshot(child)

    @contextmanager
    def slot(self, semaphore, context=None):
        while not semaphore.acquire(timeout=0.25):
            if context:
                context.check_cancelled()
            if self.cancelled.is_set():
                raise RuntimeError("Task tree cancelled")
        try:
            if self.cancelled.is_set():
                raise RuntimeError("Task tree cancelled")
            yield
        finally:
            semaphore.release()

    def delegate(
        self, parent: TaskContext, workspace: Path, tasks: list[dict], *, cancel=None
    ) -> dict:
        parent.check_cancelled()
        if parent.depth >= self.settings.max_depth:
            raise ValueError("Maximum task depth reached; complete this component locally")
        if not isinstance(tasks, list) or not tasks:
            raise ValueError("tasks must be a nonempty list")
        for task in tasks:
            if not isinstance(task, dict) or task.get("kind") not in KINDS:
                raise ValueError(f"Each task must have kind in {KINDS}")
            unknown = set(task) - {"kind", "goal", "acceptance", "inputs", "skills"}
            if unknown:
                raise ValueError(f"Unknown task fields: {sorted(unknown)}")
            for name in ("goal", "acceptance"):
                if not isinstance(task.get(name), str) or not task[name].strip():
                    raise ValueError(f"Each task requires nonempty {name}")
            for path in text_list(task.get("inputs"), "inputs"):
                local_path(workspace, path)
            for name in text_list(task.get("skills", []), "skills"):
                self.skill_registry.get(name)
        with self.lock:
            parent.check_cancelled()
            if error := self._admission_error(parent, len(tasks)):
                return error
            if len(self.tasks) + len(tasks) > self.settings.max_agents:
                raise ValueError("Total task-count limit reached (failed tasks also count)")
            children = [
                TaskContext(
                    self,
                    uuid.uuid4().hex,
                    parent.task_id,
                    parent.depth + 1,
                    task["kind"],
                    skills=tuple(task.get("skills", [])),
                    parent_cancel=cancel,
                )
                for task in tasks
            ]
            self.tasks.update({child.task_id: child for child in children})
            self._enqueue(
                parent,
                {"type": "agent_tasks_started", "tasks": [self._snapshot(c) for c in children]},
            )
        try:
            with ThreadPoolExecutor(max_workers=len(children)) as pool:
                try:
                    futures = [
                        pool.submit(self._run_child, parent, child, workspace, task)
                        for (child, task) in zip(children, tasks)
                    ]
                    results = [future.result() for future in futures]
                except BaseException:
                    for child in children:
                        child.stopped.set()
                        child.wakeup.set()
                    raise
        except BaseException:
            with self.lock:
                for child in children:
                    if child.state == "running":
                        (child.state, child.phase, child.status) = ("idle", "finished", "error")
                        child.result = {
                            "task_id": child.task_id,
                            "activation": child.activation,
                            "status": "error",
                            "state": "idle",
                            "can_resume": False,
                            "message": "Child worker could not complete startup",
                        }
                        self._enqueue(parent, {"type": "agent_task_finished", **child.result})
            raise
        return {
            "status": "success"
            if all((r["status"] == "completed" for r in results))
            else "partial",
            "tasks": results,
            "next_step": "Inspect manifests, assemble received components, then render and review the whole.",
        }

    def _run_child(self, parent, child, workspace, task):
        directory = self.episode / "agents" / child.task_id
        child_workspace = directory / "workspace"
        activation_dir = directory / "activations" / f"{child.activation:04d}"
        resuming = child.activation > 1
        result = {
            "task_id": child.task_id,
            "kind": child.kind,
            "activation": child.activation,
            "status": "error",
        }
        try:
            child.check_cancelled()
            activation_dir.mkdir(parents=True)
            child_workspace.mkdir(parents=True, exist_ok=resuming)
            inputs = (
                _revision_target(child_workspace / "inputs", child.activation)
                if resuming
                else child_workspace / "inputs"
            )
            input_prefix = inputs.relative_to(child_workspace)
            inputs.mkdir(parents=True)
            hashes = copy_files(workspace, inputs, task["inputs"])
            spec = {
                **task,
                "task_id": child.task_id,
                "parent_id": parent.task_id,
                "depth": child.depth,
                "input_sha256": hashes,
                "max_turns": self.settings.max_turns,
                "activation": child.activation,
                "input_prefix": input_prefix.as_posix(),
            }
            specification = json.dumps(spec, ensure_ascii=False, indent=2)
            (activation_dir / "task.json").write_text(specification, encoding="utf-8")
            if not resuming:
                (directory / "task.json").write_text(specification, encoding="utf-8")
                goal = f"Task: {task['goal']}\nAcceptance criteria: {task['acceptance']}\n"
            else:
                goal = f"Resume task {child.task_id}, activation {child.activation}. Continue your existing task and revise the delivery. Parent feedback: {task['feedback']}\nPrefer the newly supplied input versions where relevant. Inspect previous files and tool results before making changes; do not replay old tools. Render and inspect revised asset/camera results before submitting a new delivery.\n"
            goal += f"New input snapshot files under {input_prefix.as_posix()}/: {json.dumps(list(hashes), ensure_ascii=False)}"
            child.check_cancelled()
            child.phase = "working"
            completed = self.runner(child_workspace, goal, child)
            child.check_cancelled()
            if completed.get("status") != "completed":
                result.update(
                    status=completed.get("status", "error"),
                    message=completed.get("message", "Child did not submit a delivery"),
                )
            else:
                source_manifest = local_path(child_workspace, completed["manifest"])
                manifest = read_delivery(source_manifest)
                staging = workspace / ".harness" / f"receive_{child.task_id}"
                try:
                    hashes = copy_files(
                        source_manifest.parent, staging, ["manifest.json", *manifest["files"]]
                    )
                    base = workspace / "imports" / child.task_id
                    target = (
                        base if child.activation == 1 else _revision_target(base, child.activation)
                    )
                    target.parent.mkdir(parents=True, exist_ok=True)
                    staging.rename(target)
                    if child.kind != "review":
                        with self.lock:
                            parent.assembly_revision += 1
                finally:
                    if staging.exists():
                        shutil.rmtree(staging)
                result.update(
                    status="completed",
                    summary=manifest.get("summary", ""),
                    manifest=str((target / "manifest.json").relative_to(workspace)),
                    remaining_issues=manifest.get("remaining_issues", []),
                    sha256=hashes,
                )
        except Exception as exc:
            result.update(
                status="interrupted" if child.is_cancelled() else "error",
                type=type(exc).__name__,
                message=str(exc),
            )
        finally:
            result.update(
                state="idle", can_resume=(child_workspace / ".harness" / "history.json").is_file()
            )
            try:
                if directory.exists():
                    (directory / "result.json").write_text(
                        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
                    if activation_dir.exists():
                        (activation_dir / "result.json").write_text(
                            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
                        )
                        request_path = child_workspace / ".harness" / "request.json"
                        if request_path.is_file():
                            saved = json.loads(request_path.read_text(encoding="utf-8"))
                            if saved.get("activation") == child.activation:
                                shutil.copyfile(request_path, activation_dir / "request.json")
                                session_result = child_workspace / ".harness" / "result.json"
                                if session_result.is_file():
                                    shutil.copyfile(
                                        session_result, activation_dir / "session_result.json"
                                    )
            except (OSError, ValueError) as exc:
                result["audit_error"] = str(exc)
                logging.exception("Could not save child activation audit")
            with self.lock:
                child.status = result["status"]
                (child.state, child.phase) = ("idle", "finished")
                child.result = result
                self._enqueue(parent, {"type": "agent_task_finished", **result})
        return result

    def resume(
        self,
        parent: TaskContext,
        workspace: Path,
        task_id: str,
        feedback: str,
        inputs: list[str],
        *,
        cancel=None,
    ) -> dict:
        parent.check_cancelled()
        if not isinstance(task_id, str) or not isinstance(feedback, str) or (not feedback.strip()):
            raise ValueError("A child task_id and nonempty feedback are required")
        for path in text_list(inputs, "inputs"):
            local_path(workspace, path)
        with self.lock:
            if self.cancelled.is_set():
                raise ValueError("Task tree is cancelled")
            if self.tasks.get(parent.task_id) is not parent or parent.state != "running":
                raise ValueError("The parent task must be active")
            child = self.tasks.get(task_id)
            if child is None or child.parent_id != parent.task_id:
                raise ValueError(
                    "Only the direct parent can resume this child in the current episode"
                )
            if child.state != "idle":
                raise ValueError("Child task is already running")
            if error := self._admission_error(parent, 1):
                return error
            meta = self.episode / "agents" / task_id / "workspace" / ".harness"
            if not (meta / "history.json").is_file() or not (meta / "request.json").is_file():
                raise ValueError("Child has no saved conversation to resume")
            child.activation += 1
            child.assembly_revision += 1
            child.state = "running"
            child.status = "running"
            child.phase = "preparing"
            child.result = None
            child.stopped.clear()
            child.parent_cancel = cancel
            self._enqueue(parent, {"type": "agent_tasks_started", "tasks": [self._snapshot(child)]})
        return self._run_child(parent, child, workspace, {"feedback": feedback, "inputs": inputs})

    def resume_root(self, interaction_round):
        """Reactivate the same case root; keep child IDs, skill snapshot and scene state."""
        with self.lock:
            if self.cancelled.is_set() or self.root.state != "idle":
                raise ValueError("The case root must be idle and the team must still be open")
            self.root.activation += 1
            self.root.interaction_round = interaction_round
            self.root.state = self.root.status = "running"
            self.root.phase = "preparing"
            self.root.stopped.clear()

    def close(self):
        self.cancelled.set()
        self.cancel_children(self.root)
        deadline = time.monotonic() + self.settings.api_timeout + 5
        while time.monotonic() < deadline:
            with self.lock:
                active = [
                    c.task_id for c in self.tasks.values() if c.is_child and c.state == "running"
                ]
            if not active:
                return
            time.sleep(0.1)
        logging.warning("Agent cleanup still pending after API timeout: %s", active)

    def save_report(self):
        with self.lock:
            records = [
                {
                    "task_id": c.task_id,
                    "parent_id": c.parent_id,
                    "kind": c.kind,
                    "depth": c.depth,
                    "model_requests": c.used,
                    "status": c.status,
                    "state": c.state,
                    "activations": c.activation,
                }
                for c in self.tasks.values()
            ]
            report = {
                "max_turns_per_agent": self.settings.max_turns,
                "model_requests": sum((c.used for c in self.tasks.values())),
                "tasks": records,
            }
        (self.episode / "team.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def submit_task(context: TaskContext, workspace: Path, manifest_path: str) -> dict:
    if not context.is_child:
        raise ValueError("Only child tasks submit deliveries; the root uses finish")
    path = local_path(workspace, manifest_path)
    if path.name != "manifest.json" or not path.is_relative_to(workspace / "delivery"):
        raise ValueError("Submit a manifest.json under delivery/")
    manifest = read_delivery(path)
    snapshot = workspace / ".harness" / "deliveries" / uuid.uuid4().hex
    try:
        copy_files(path.parent, snapshot, ["manifest.json", *manifest["files"]])
    except Exception:
        if snapshot.exists():
            shutil.rmtree(snapshot)
        raise
    return {
        "status": "completed",
        "manifest": (snapshot / "manifest.json").relative_to(workspace).as_posix(),
        "summary": manifest.get("summary", ""),
        "remaining_issues": manifest.get("remaining_issues", []),
        "task_id": context.task_id,
        "activation": context.activation,
    }
