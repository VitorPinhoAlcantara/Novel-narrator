import threading
import time

from novel_narrator.log import format_duration

_listeners = []

FILE_OUTPUTS = {"pdf": "PDF", "cbz": "CBZ"}


def emit(kind, **data):
    for listener in list(_listeners):
        listener(kind, data)


def add_listener(listener):
    _listeners.append(listener)


def remove_listener(listener):
    if listener in _listeners:
        _listeners.remove(listener)


def chapter_key(chapter):
    return (str(chapter.volume).strip(), chapter.number)


class Tracker:
    def __init__(self):
        self.lock = threading.RLock()
        self.outputs = set()
        self.chapters = {}
        self.feed = []
        self.current = None
        self.downloading = None
        self.audio_times = []
        self.started = time.time()

    def __call__(self, kind, data):
        handler = getattr(self, f"_on_{kind}", None)
        if handler:
            with self.lock:
                handler(**data)

    def _on_plan(self, chapters, outputs, download, audio, skipped):
        self.outputs = set(outputs)
        download, audio, skipped = map(lambda items: {chapter_key(c) for c in items}, (download, audio, skipped))
        for chapter in chapters:
            key = chapter_key(chapter)
            entry = self.chapters.setdefault(
                key,
                {
                    "key": key,
                    "number": chapter.number,
                    "title": chapter.title,
                    "volume": key[0],
                    "dl": None,
                    "audio": None,
                    "audio_info": None,
                    "reason": "",
                    "state": "wait",
                },
            )
            entry["dl"] = "wait" if key in download else ("ok" if entry["dl"] == "ok" else None)
            entry["audio"] = "wait" if key in audio else None
            entry["state"] = "skip" if key in skipped else "wait"
            entry["reason"] = ""

    def _on_download(self, chapter, state, reason=""):
        entry = self.chapters.get(chapter_key(chapter))
        if entry:
            entry["dl"] = state
            entry["reason"] = reason or entry["reason"]
        self.downloading = chapter if state == "run" else self.downloading
        if state != "run" and self.downloading is chapter:
            self.downloading = None

    def _on_audio_start(self, chapter):
        entry = self.chapters.get(chapter_key(chapter))
        if entry:
            entry["audio"] = "run"
        self.current = {
            "key": chapter_key(chapter),
            "number": chapter.number,
            "title": chapter.title,
            "ids": [],
            "status": {},
            "active": {},
            "stage": "preparando o texto",
        }

    def _on_segments(self, ids, done):
        if self.current:
            self.current["ids"] = list(ids)
            self.current["status"] = {i: "d" for i in done}
            self.current["stage"] = ""

    def _on_segment_start(self, id, text, worker):
        if self.current:
            self.current["active"][worker] = (id, text)
            self.current["status"][id] = "a"
            self.current["stage"] = ""

    def _on_segment_done(self, id, ok):
        if self.current:
            self.current["status"][id] = "d" if ok else "x"
            for worker, (active_id, _) in list(self.current["active"].items()):
                if active_id == id:
                    del self.current["active"][worker]

    def _on_stage(self, text):
        if self.current:
            self.current["stage"] = text
            self.current["active"] = {}

    def _on_audio_done(self, chapter, minutes, seconds, alerts, report=""):
        entry = self.chapters.get(chapter_key(chapter))
        if entry:
            entry["audio"] = "warn" if alerts else "ok"
            entry["audio_info"] = {"minutes": minutes, "seconds": seconds, "alerts": alerts, "report": report}
        self.audio_times.append(seconds)

    def _on_chapter(self, chapter, ok, reason=""):
        key = chapter_key(chapter)
        entry = self.chapters.get(key)
        if entry is None:
            return
        if ok:
            entry["state"] = "warn" if entry["audio"] == "warn" else "ok"
        else:
            entry["state"], entry["reason"] = "bad", reason
            if entry["audio"] == "run":
                entry["audio"] = "bad"
        if self.current and self.current["key"] == key:
            self.current = None
        self.feed.append((time.time(), entry))
        del self.feed[:-50]

    def _on_finish(self):
        self.current = None
        self.downloading = None

    def snapshot(self):
        with self.lock:
            entries = sorted(self.chapters.values(), key=lambda e: (_volume_order(e["volume"]), e["number"]))
            dl = [e["dl"] for e in entries if e["dl"] is not None]
            audio = [e["audio"] for e in entries if e["audio"] is not None]
            audio_left = sum(1 for a in audio if a in ("wait", "run"))
            recent = self.audio_times[-10:]
            eta = audio_left * sum(recent) / len(recent) if recent and audio_left else None
            current = None
            if self.current:
                c = self.current
                status = c["status"]
                current = {
                    "number": c["number"],
                    "title": c["title"],
                    "stage": c["stage"],
                    "segments": [status.get(i, "") for i in c["ids"]],
                    "done": sum(1 for s in status.values() if s in ("d", "x")),
                    "total": len(c["ids"]),
                    "active": [text for _, (_, text) in sorted(c["active"].items())],
                }
            volumes = {}
            for e in entries:
                volumes.setdefault(e["volume"], []).append({**e, "tile": _tile(e)})
            return {
                "outputs": set(self.outputs),
                "file_names": " + ".join(v for k, v in FILE_OUTPUTS.items() if k in self.outputs),
                "total": len(entries),
                "done": sum(1 for e in entries if e["state"] != "wait"),
                "skipped": sum(1 for e in entries if e["state"] == "skip"),
                "failed": sum(1 for e in entries if e["state"] == "bad"),
                "warned": sum(1 for e in entries if e["state"] == "warn"),
                "only_files": sum(1 for e in entries if e["dl"] is not None and e["audio"] is None),
                "with_audio": len(audio),
                "dl_total": len(dl),
                "dl_done": sum(1 for s in dl if s in ("ok", "bad")),
                "audio_total": len(audio),
                "audio_done": sum(1 for s in audio if s in ("ok", "warn", "bad")),
                "eta": format_duration(eta) if eta else "",
                "downloading": self.downloading.number if self.downloading else None,
                "current": current,
                "feed": [(t, dict(e)) for t, e in reversed(self.feed)],
                "volumes": [(name, items) for name, items in volumes.items()],
                "elapsed": format_duration(time.time() - self.started),
            }


def describe_entry(entry, outputs):
    parts = []
    files = " + ".join(v for k, v in FILE_OUTPUTS.items() if k in outputs)
    if entry["state"] == "bad":
        return [("bad", entry["reason"] or "erro")]
    if files:
        parts.append(("ok", files) if entry["dl"] == "ok" else ("skip", f"{files} já existia"))
    if "audio" in outputs:
        info = entry["audio_info"]
        if info and info["alerts"]:
            report = f" → {info['report']}" if info["report"] else ""
            parts.append(("warn", f"áudio com {info['alerts']} alerta(s){report}"))
        elif info:
            minutes = f"{info['minutes']:.1f}".replace(".", ",")
            parts.append(("ok", f"áudio {minutes} min em {format_duration(info['seconds'])}"))
        elif entry["audio"] is None:
            parts.append(("skip", "áudio já no servidor"))
    return parts


def _tile(entry):
    if entry["state"] in ("ok", "warn", "bad", "skip"):
        return entry["state"]
    if entry["audio"] == "run":
        return "run"
    if entry["dl"] == "run":
        return "dl"
    return "wait"


def _volume_order(volume):
    return (0, int(volume)) if volume.isdigit() else (1, volume)
