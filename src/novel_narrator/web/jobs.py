import itertools
import queue
import threading
import time
from dataclasses import dataclass, field

from novel_narrator.log import add_listener, remove_listener

MAX_LINES = 400


@dataclass
class Job:
    id: int
    title: str
    chapters: list
    outputs: set
    task: object
    status: str = "queued"
    done: int = 0
    total: int = 0
    lines: list = field(default_factory=list)
    failures: list = field(default_factory=list)
    error: str = ""
    created: float = field(default_factory=time.time)
    remaining: list = field(default_factory=list)
    base: int = 0
    stop: threading.Event = field(default_factory=threading.Event)
    on_stop: list = field(default_factory=list)

    @property
    def active(self):
        return self.status in ("queued", "running", "stopping")

    @property
    def percent(self):
        return int(100 * self.done / self.total) if self.total else 0

    def progress(self, done, total):
        self.done, self.total = self.base + done, self.base + total


class JobQueue:
    def __init__(self):
        self.jobs = {}
        self._pending = queue.Queue()
        self._ids = itertools.count(1)
        threading.Thread(target=self._work, daemon=True).start()

    def submit(self, title, chapters, outputs, task):
        job = Job(next(self._ids), title, list(chapters), set(outputs), task, total=len(chapters))
        self.jobs[job.id] = job
        self._pending.put(job)
        return job

    def pause(self, job):
        if job.status == "queued":
            job.remaining, job.status = list(job.chapters), "paused"
        elif job.status == "running":
            job.status = "stopping"
            job.stop.set()
            for hook in job.on_stop:
                hook()

    def resume(self, job):
        if job.status != "paused" or not job.remaining:
            return
        job.chapters, job.remaining, job.base = job.remaining, [], job.done
        job.stop, job.on_stop = threading.Event(), []
        job.status = "queued"
        self._pending.put(job)

    def retry(self, job):
        return self.submit(job.title, [f["chapter"] for f in job.failures], job.outputs, job.task)

    def recent(self):
        return sorted(self.jobs.values(), key=lambda j: j.id, reverse=True)

    def _work(self):
        while True:
            job = self._pending.get()
            if job.status == "queued":
                self.run(job)

    def run(self, job):
        def listen(tag, message):
            job.lines.append(f"[{tag}] {message}")
            del job.lines[:-MAX_LINES]

        job.status = "running"
        add_listener(listen)
        try:
            result = job.task(job)
            job.failures += result["failures"]
            job.remaining = result.get("stopped", [])
            if job.remaining:
                job.status = "paused"
            else:
                job.status = "failed" if job.failures else "done"
        except Exception as exc:
            job.error = str(exc)
            job.status = "failed"
        finally:
            remove_listener(listen)
