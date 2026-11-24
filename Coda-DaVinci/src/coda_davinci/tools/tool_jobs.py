"""Agent-local background tools, periodic notices and owned-process interruption."""

import json
import logging
import os
from pathlib import Path
import signal
import threading
import time
import uuid
from coda_davinci.tools.tool_results import ToolResult

LOG = logging.getLogger(__name__)
LONG_TOOLS = frozenset({"run_blender", "render_image", "render_sequence"})
CONTROL_TOOLS = frozenset(
    {"wait_tool", "interrupt_tool", "send_message", "list_agents", "wait_agent", "interrupt_agent"}
)
START_YIELD_MS = 0
WAIT_YIELD_MS = 10000
MAX_YIELD_MS = 60000


def wait_seconds(yield_time_ms, default):
    """Validate before launching a job; yielding never cancels its process."""
    value = default if yield_time_ms is None else yield_time_ms
    if type(value) is not int or not 0 <= value <= MAX_YIELD_MS:
        raise ValueError(f"yield_time_ms must be an integer from 0 to {MAX_YIELD_MS}, or null")
    return value / 1000


REMINDER = "This tool is still running. Check its log and source before deciding to wait or interrupt. Look for repeated construction of many objects and repeated bpy.ops/scene updates; consider shared meshes, Collection instances and batched bpy.data construction. For sequences, check frame_start/end/step, timeline FPS, duration and total frame count. Sequence manifests record timeline FPS divided by fps_base and frame_step; encode_video inherits this rate when fps=null, so do not lower FPS twice. You may use other tools, wait_tool(job_id), or interrupt_tool(job_id). Interrupt before editing files the running job may still read, or write a new version."


class ToolInterrupted(RuntimeError):
    pass


def kill_process_group(process):
    """Only call with a Popen owned by this execution; never accept a model-supplied PID."""
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


class ToolJob:
    def __init__(self, manager, name, arguments):
        self.manager = manager
        self.job_id = uuid.uuid4().hex
        self.name = name
        self.arguments = arguments
        self.created = manager.clock()
        self.next_notice = manager.first_notice
        self.cancelled = threading.Event()
        self.done = False
        self.yielded = False
        self.result = None
        self.payload = None
        self.process = None
        self.pid = None
        self.log = None
        self.phase = "queued"
        self.events = []
        self.thread = None
        self.directory = manager.meta / "tool_jobs" / self.job_id
        self.directory.mkdir(parents=True)
        (self.directory / "request.json").write_text(
            json.dumps(
                {"job_id": self.job_id, "tool": name, "arguments": arguments},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def snapshot(self):
        data = {
            "job_id": self.job_id,
            "tool": self.name,
            "arguments": self.arguments,
            "status": "running",
            "phase": self.phase,
            "pid": self.pid,
            "pgid": self.pid,
            "process_attached": self.process is not None,
            "elapsed_seconds": round(self.manager.clock() - self.created, 2),
        }
        if self.log:
            data["log"] = self.log
            try:
                with (self.manager.root / self.log).open("rb") as stream:
                    stream.seek(max(0, stream.seek(0, 2) - 4000))
                    data["log_tail"] = stream.read().decode("utf-8", errors="replace")
            except OSError as exc:
                data["log_read_error"] = str(exc)
        if self.done:
            data.update(self.result)
            if self.payload and self.payload.images:
                data["image_outputs"] = [
                    {"path": str(image.path.relative_to(self.manager.root)), "label": image.label}
                    for image in self.payload.images
                ]
                data["next_step"] = "Call wait_tool with this job_id to receive the actual images."
            if self.payload and self.payload.finished:
                data["next_step"] = (
                    "Call wait_tool with this job_id to collect and acknowledge final delivery."
                )
        elif self.cancelled.is_set():
            data["status"] = "interrupt_requested"
        return data

    def record(self, event, data):
        with (self.directory / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"event": event, **data}, ensure_ascii=False) + "\n")

    def tick(self):
        with self.manager.changed:
            if self.done or self.cancelled.is_set() or self.name not in LONG_TOOLS:
                return
            elapsed = self.manager.clock() - self.created
            while elapsed >= self.next_notice:
                notice = {
                    **self.snapshot(),
                    "reminder": REMINDER,
                    "reminder_at_seconds": self.next_notice,
                }
                if self.phase == "queued":
                    notice["reminder"] = (
                        "Waiting for a process slot; no new process is executing yet. " + REMINDER
                    )
                self.events.append(notice)
                self.record("reminder", notice)
                self.next_notice += self.manager.repeat_notice
            self.manager.changed.notify_all()

    def check_cancelled(self):
        if self.cancelled.is_set() or self.manager.stopping.is_set():
            raise ToolInterrupted("Tool interrupted by its owner")

    def attach(self, process, log, phase):
        with self.manager.changed:
            (self.process, self.pid, self.log, self.phase) = (process, process.pid, log, phase)
            self.record("process_started", self.snapshot())
            LOG.info(
                "Tool process started: %s | job=%s | phase=%s | pid=%s | log=%s",
                self.name,
                self.job_id,
                phase,
                self.pid,
                log,
            )
            if self.cancelled.is_set() or self.manager.stopping.is_set():
                kill_process_group(process)
            self.manager.changed.notify_all()

    def detach(self, process):
        with self.manager.changed:
            if self.process is process:
                self.process = None
                self.phase = "finalizing"
            self.manager.changed.notify_all()


class ToolJobs:
    def __init__(
        self,
        root: Path,
        meta: Path,
        *,
        first_notice=120.0,
        repeat_notice=60.0,
        clock=time.monotonic,
    ):
        (self.root, self.meta) = (root, meta)
        (self.first_notice, self.repeat_notice, self.clock) = (first_notice, repeat_notice, clock)
        self.changed = threading.Condition(threading.RLock())
        self.local = threading.local()
        self.jobs = {}
        self.stopping = threading.Event()
        self.wakeup = threading.Event()

    def current(self):
        return getattr(self.local, "job", None)

    def has_active(self, *, exclude_current=False):
        current = self.current() if exclude_current else None
        with self.changed:
            return any((not job.done and job is not current for job in self.jobs.values()))

    def payload(self, job_id):
        with self.changed:
            job = self._get(job_id)
            return job.payload if job.done else None

    def _get(self, job_id):
        if not isinstance(job_id, str) or job_id not in self.jobs:
            raise ValueError("Unknown job_id; only this agent's tool jobs can be controlled")
        return self.jobs[job_id]

    def start(self, name, arguments, callback, *, yield_time_ms=None):
        wait_seconds(yield_time_ms, START_YIELD_MS)
        with self.changed:
            if self.stopping.is_set():
                raise RuntimeError("Tool session is closing")
            job = ToolJob(self, name, arguments)
            self.jobs[job.job_id] = job
            LOG.info(
                "Background tool starting: %s | job=%s | workspace=%s", name, job.job_id, self.root
            )
            job.thread = threading.Thread(
                target=self._work, args=(job, callback), name=f"tool-{job.job_id}", daemon=True
            )
            job.thread.start()
        return self.wait(job.job_id, START_YIELD_MS if yield_time_ms is None else yield_time_ms)

    def _work(self, job, callback):
        self.local.job = job
        try:
            job.check_cancelled()
            if job.name not in LONG_TOOLS:
                job.phase = "working"
            result = callback()
            if isinstance(result, ToolResult):
                job.payload = result
                result = result.data
            result = {"status": "success", **result}
        except ToolInterrupted as exc:
            result = {"status": "interrupted", "message": str(exc)}
        except Exception as exc:
            result = {"status": "error", "type": type(exc).__name__, "message": str(exc)}
            LOG.exception("Background tool %s failed", job.job_id)
        finally:
            self.local.job = None
        with self.changed:
            if job.cancelled.is_set():
                result = {
                    **result,
                    "status": "interrupted",
                    "message": "Tool interrupted by its owner",
                }
                job.payload = None
            (job.result, job.done, job.phase) = (result, True, "finished")
            outcome = job.snapshot()
            job.events.append(outcome)
            job.record("completed", outcome)
            (job.directory / "result.json").write_text(
                json.dumps(outcome, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            LOG.info(
                "Background tool finished: %s | job=%s | status=%s | elapsed=%.1fs",
                job.name,
                job.job_id,
                result["status"],
                self.clock() - job.created,
            )
            self.changed.notify_all()

    def wait(self, job_id, yield_time_ms=None):
        deadline = time.monotonic() + wait_seconds(yield_time_ms, WAIT_YIELD_MS)
        with self.changed:
            job = self._get(job_id)
            while True:
                job.tick()
                if job.done:
                    job.events.clear()
                    return job.snapshot()
                if job.events:
                    notice = job.events[-1]
                    job.events.clear()
                    job.yielded = True
                    return notice
                if self.wakeup.is_set():
                    job.yielded = True
                    return {
                        **job.snapshot(),
                        "message": "An agent message is waiting; return to the model.",
                    }
                if any(
                    (
                        other.yielded and other.events
                        for other in self.jobs.values()
                        if other is not job
                    )
                ):
                    job.yielded = True
                    return {**job.snapshot(), "message": "Another background tool has an update"}
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    job.yielded = True
                    result = job.snapshot()
                    job.record("yielded", result)
                    return result
                self.changed.wait(timeout=min(0.25, remaining))

    def messages(self, *, wait=False):
        deadline = time.monotonic() + WAIT_YIELD_MS / 1000
        with self.changed:
            while True:
                updates = []
                for job in self.jobs.values():
                    job.tick()
                    if job.yielded:
                        updates.extend(job.events)
                        job.events.clear()
                if updates:
                    return [
                        {
                            "role": "user",
                            "content": "Background tool updates:\n"
                            + json.dumps(updates, ensure_ascii=False),
                        }
                    ]
                remaining = deadline - time.monotonic()
                if not wait or not self.has_active() or remaining <= 0 or self.wakeup.is_set():
                    return []
                self.changed.wait(timeout=min(0.25, remaining))

    def interrupt(self, job_id):
        with self.changed:
            job = self._get(job_id)
            if not job.done:
                job.cancelled.set()
                if job.process is not None:
                    kill_process_group(job.process)
                job.record("interrupt_requested", job.snapshot())
                self.changed.notify_all()
            job.events.clear()
            return job.snapshot()

    def close(self):
        self.stopping.set()
        with self.changed:
            active = [job for job in self.jobs.values() if not job.done]
            for job in active:
                job.cancelled.set()
                if job.process is not None:
                    kill_process_group(job.process)
            self.changed.notify_all()
        deadline = time.monotonic() + 5
        for job in active:
            job.thread.join(timeout=max(0, deadline - time.monotonic()))
            if job.thread.is_alive():
                LOG.error("Tool %s has not finished cleanup; last PID=%s", job.job_id, job.pid)
