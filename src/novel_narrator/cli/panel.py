import os
import shutil
import sys
import threading

from novel_narrator.log import set_printer
from novel_narrator.pipeline import progress
from novel_narrator.pipeline.progress import Tracker, describe_entry

RESET = "\x1b[0m"
COLORS = {
    "ok": "\x1b[32m",
    "warn": "\x1b[33m",
    "bad": "\x1b[31m",
    "skip": "\x1b[34m",
    "accent": "\x1b[36m",
    "dim": "\x1b[2m",
    "bold": "\x1b[1m",
    "": "",
}
MARKS = {"ok": ("ok", "✓"), "warn": ("warn", "!"), "bad": ("bad", "✗")}
SHOWN_CHAPTER_WORDS = ("INTERROMPIDO", "AVISO", "FALHA")


def clip(parts, width):
    out, used = [], 0
    for color, text in parts:
        room = width - used
        if room <= 0:
            break
        if len(text) > room:
            text = text[: max(0, room - 1)] + "…"
        used += len(text)
        out.append(f"{COLORS[color]}{text}{RESET}" if color else text)
    return "".join(out)


def bar(done, total, size=24):
    filled = int(size * done / total) if total else 0
    return "█" * filled + "░" * (size - filled)


def chapter_line(entry, outputs):
    color, mark = MARKS.get(entry["state"], ("dim", "·"))
    parts = [(color, f"{mark} "), ("bold", f"{entry['number']:>5}  "), ("", f"{entry['title'][:36]:<36}  ")]
    for kind, text in describe_entry(entry, outputs):
        parts += [(kind, text), ("", "  ")]
    return parts


def footer(snapshot, width):
    rows = [[("dim", "─" * min(width, 78))]]
    if snapshot["dl_total"]:
        row = [("accent", "Downloads "), ("", bar(snapshot["dl_done"], snapshot["dl_total"]))]
        row.append(("bold", f"  {snapshot['dl_done']}/{snapshot['dl_total']}"))
        if snapshot["downloading"] is not None:
            row.append(("dim", f"   cap {snapshot['downloading']}"))
        rows.append(row)
    if snapshot["audio_total"]:
        row = [("accent", "Áudio     "), ("", bar(snapshot["audio_done"], snapshot["audio_total"]))]
        row.append(("bold", f"  {snapshot['audio_done']}/{snapshot['audio_total']}"))
        current = snapshot["current"]
        details = []
        if current:
            details.append(f"cap {current['number']}")
            details.append(current["stage"] or f"trechos {current['done']}/{current['total']}")
        if snapshot["eta"]:
            details.append(f"~{snapshot['eta']}")
        if details:
            row.append(("dim", "   " + " · ".join(details)))
        rows.append(row)
        for text in current["active"] if current else []:
            rows.append([("accent", "  ♪ "), ("", text)])
    if not snapshot["dl_total"] and not snapshot["audio_total"]:
        rows.append([("accent", "Capítulos "), ("", bar(snapshot["done"], snapshot["total"]))])
    rows.append([("dim", "Ctrl+C pausa (o áudio continua do trecho em que parou)")])
    return [clip(row, width) for row in rows]


class Panel:
    def __init__(self, stream=None, interval=0.25):
        self.out = stream or sys.stdout
        self.interval = interval
        self.tracker = Tracker()
        self.lock = threading.RLock()
        self.buffer = ""
        self.drawn = 0
        self.active = False
        self._stop = threading.Event()

    def __enter__(self):
        if not self.out.isatty():
            return self
        if os.name == "nt":
            os.system("")
        self.active = True
        self._original = sys.stdout
        sys.stdout = self
        set_printer(self._log)
        progress.add_listener(self.tracker)
        progress.add_listener(self._event)
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        if not self.active:
            return
        self._stop.set()
        self._thread.join()
        progress.remove_listener(self._event)
        progress.remove_listener(self.tracker)
        set_printer(None)
        with self.lock:
            self._clear()
            if self.buffer:
                self.out.write(self.buffer + "\n")
                self.buffer = ""
            self.out.flush()
        sys.stdout = self._original
        self.active = False

    def write(self, text):
        with self.lock:
            self.buffer += text
            if "\n" not in self.buffer:
                return len(text)
            lines, self.buffer = self.buffer.rsplit("\n", 1)
            self._clear()
            self.out.write(lines + "\n")
            self._draw()
        return len(text)

    def flush(self):
        self.out.flush()

    def isatty(self):
        return True

    def _width(self):
        return shutil.get_terminal_size().columns - 1

    def _log(self, tag, message):
        if tag.startswith("Cap ") and not any(word in message for word in SHOWN_CHAPTER_WORDS):
            return
        self.write(clip([("dim", f"[{tag}] {message}")], self._width()) + "\n")

    def _event(self, kind, data):
        if kind != "chapter":
            return
        snapshot = self.tracker.snapshot()
        entry = next((e for _, e in snapshot["feed"]), None)
        if entry:
            self.write(clip(chapter_line(entry, snapshot["outputs"]), self._width()) + "\n")

    def _loop(self):
        while not self._stop.wait(self.interval):
            with self.lock:
                self._clear()
                self._draw()

    def _clear(self):
        if self.drawn:
            self.out.write(f"\x1b[{self.drawn}F\x1b[J")
            self.drawn = 0

    def _draw(self):
        if not self.tracker.chapters:
            return
        rows = footer(self.tracker.snapshot(), self._width())
        self.out.write("\n".join(rows) + "\n")
        self.out.flush()
        self.drawn = len(rows)
