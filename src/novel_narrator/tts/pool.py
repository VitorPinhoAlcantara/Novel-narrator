import json
import os
import queue
import subprocess
import sys
import threading
import time

from novel_narrator.log import log
from novel_narrator.tts import gpu


class WorkerProcess:
    def __init__(self, index, command, log_dir):
        self.index = index
        os.makedirs(log_dir, exist_ok=True)
        self._log = open(os.path.join(log_dir, f"worker_{index}.log"), "w", encoding="utf-8")
        env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        self.proc = subprocess.Popen(
            command,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._log,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        self.ready = None
        self.dead = False

    def wait_ready(self):
        while True:
            line = self.proc.stdout.readline()
            if not line:
                raise RuntimeError(f"worker {self.index} terminou antes de ficar pronto (veja o log)")
            event = json.loads(line)
            if event.get("event") == "ready":
                self.ready = event
                return event

    def send(self, job):
        try:
            self.proc.stdin.write(json.dumps(job, ensure_ascii=False) + "\n")
            self.proc.stdin.flush()
            line = self.proc.stdout.readline()
        except (OSError, ValueError):
            line = ""
        if not line:
            self.dead = True
            return None
        return json.loads(line)

    def close(self):
        try:
            self.proc.stdin.write(json.dumps({"cmd": "quit"}) + "\n")
            self.proc.stdin.flush()
        except (OSError, ValueError):
            pass
        try:
            self.proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self._log.close()


class TTSPool:
    def __init__(self, config, voice_paths, workers=None, log_dir=None):
        self.config = config
        self.tts = config["tts"]
        self.voice_paths = list(voice_paths)
        self.n_workers = max(1, int(workers or self.tts.get("workers", 1)))
        self.log_dir = log_dir or os.path.join(config["paths"]["data_dir"], "logs_tts")
        self.workers = []
        self.asr = None
        self.live_limit = self.n_workers

    def _python(self):
        return self.config["paths"]["tts_python"] or sys.executable

    def _asr_command(self):
        return [
            self._python(),
            "-u",
            "-m",
            "novel_narrator.tts.asr_worker",
            "--model",
            self.tts["asr"]["model"],
            "--device",
            self.tts["device"],
        ]

    def _command(self):
        paths = self.config["paths"]
        emotion_dir = os.path.join(paths["voices_dir"], "emotion") if self.tts["emotion"]["enabled"] else ""
        cmd = [
            self._python(),
            "-u",
            "-m",
            "novel_narrator.tts.worker",
            "--model-dir",
            os.path.abspath(paths["model_dir"]),
            "--language",
            self.tts["language"],
            "--device",
            self.tts["device"],
            "--latents-cache",
            os.path.abspath(os.path.join(paths["data_dir"], "latents")),
            "--reference",
            json.dumps(self.tts["reference"]),
            "--params",
            json.dumps(self.tts["xtts"]),
            "--qa",
            json.dumps(self.tts["qa"]),
            "--matmul",
            self.tts["matmul"],
        ]
        if emotion_dir:
            cmd += ["--emotion-dir", os.path.abspath(emotion_dir)]
        for voice in self.voice_paths:
            cmd += ["--voice", os.path.abspath(voice)]
        return cmd

    def transcribe(self, items):
        if self.asr is None:
            self.asr = WorkerProcess("asr", self._asr_command(), self.log_dir)
            self.asr.wait_ready()
        texts = {}
        for item_id, wav in items:
            response = self.asr.send({"id": item_id, "wav": wav})
            texts[item_id] = response.get("text", "") if response else ""
        return texts

    def start(self):
        command = self._command()
        try:
            for i in range(self.n_workers):
                self.workers.append(WorkerProcess(i, command, self.log_dir))
            for worker in self.workers:
                worker.wait_ready()
        except Exception:
            self.close()
            raise
        self.live_limit = len(self.workers)
        return [w.ready for w in self.workers]

    def resize(self, n):
        n = max(1, int(n))
        self._prune_dead()
        while len(self.workers) > n:
            self.workers.pop().close()
        command = self._command()
        new = []
        used = {w.index for w in self.workers}
        try:
            while len(self.workers) + len(new) < n:
                index = next(i for i in range(n + len(used)) if i not in used)
                used.add(index)
                new.append(WorkerProcess(index, command, self.log_dir))
            for worker in new:
                worker.wait_ready()
        except Exception as exc:
            log("XTTS", f"AVISO: nao foi possivel iniciar mais workers: {exc}")
            for worker in new:
                worker.close()
            new = []
        self.workers.extend(new)
        self.n_workers = len(self.workers)
        self.live_limit = self.n_workers
        return self.n_workers

    def close(self):
        for worker in self.workers:
            worker.close()
        self.workers = []
        if self.asr is not None:
            self.asr.close()
            self.asr = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.close()

    def generate(self, jobs, on_done=None):
        pending = queue.Queue()
        for job in jobs:
            pending.put(job)
        results = {}
        lock = threading.Lock()
        total = len(jobs)
        positions = {}
        stop = threading.Event()
        if self.tts.get("adaptive_workers", True) and len(self.workers) > 1:
            threading.Thread(target=self._watch_memory, args=(stop,), daemon=True).start()

        def loop(worker):
            position = positions[id(worker)]
            while True:
                while position >= self.live_limit and not pending.empty():
                    time.sleep(2)
                try:
                    job = pending.get_nowait()
                except queue.Empty:
                    return
                response = worker.send(job)
                died = response is None
                if died and job.get("_deaths", 0) < 1:
                    job["_deaths"] = job.get("_deaths", 0) + 1
                    pending.put(job)
                    return
                if died:
                    response = _failed(job["id"], "worker_died")
                with lock:
                    results[job["id"]] = response
                    done = len(results)
                if on_done:
                    on_done(response, done, total)
                if died:
                    return

        while not pending.empty() and self.workers:
            positions.update({id(w): i for i, w in enumerate(self.workers)})
            threads = [threading.Thread(target=loop, args=(w,), daemon=True) for w in self.workers]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self._prune_dead()
        stop.set()

        while not pending.empty():
            job = pending.get_nowait()
            results[job["id"]] = _failed(job["id"], "no_worker")
        return results

    def _prune_dead(self):
        alive = []
        for worker in self.workers:
            if not worker.dead and worker.proc.poll() is None:
                alive.append(worker)
            else:
                worker.close()
        self.workers = alive

    def _watch_memory(self, stop):
        critical = self.tts.get("vram_critical_mb", 1000)
        headroom = self.tts.get("vram_headroom_mb", 2500)
        while not stop.wait(5):
            try:
                current = gpu.sample()
            except (OSError, ValueError, IndexError, subprocess.SubprocessError):
                continue
            free = current["total_mb"] - current["used_mb"]
            previous = self.live_limit
            if free < critical and self.live_limit > 1:
                self.live_limit -= 1
            elif free > headroom and self.live_limit < len(self.workers):
                self.live_limit += 1
            if self.live_limit != previous:
                log("GPU", f"VRAM livre {free} MB: workers de XTTS ativos {previous} -> {self.live_limit}")


def _failed(job_id, reason):
    return {
        "event": "result",
        "id": job_id,
        "ok": False,
        "attempts": 0,
        "metrics": {},
        "reasons": [reason],
        "time_s": 0,
    }
