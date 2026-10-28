import threading
import time

from novel_narrator.models import Chapter
from novel_narrator.pipeline.runner import run_chapters
from novel_narrator.tts.pool import Interrupted

LOOKAHEAD = 6


class FakeAudio:
    def __init__(self, events, fetched, lock, existing=()):
        self.events = events
        self.fetched = fetched
        self.lock = lock
        self.existing = set(existing)
        self.done = 0
        self.max_ahead = 0
        self.closed = False

    def pending(self, chapter, overwrite=False):
        return chapter.number not in self.existing

    def generate_chapter(self, chapter):
        with self.lock:
            self.events.append(("audio", chapter.number))
            self.max_ahead = max(self.max_ahead, len(self.fetched) - self.done)
        time.sleep(0.05)
        with self.lock:
            self.done += 1
        return True, None

    def close(self):
        self.closed = True


def chapters(n):
    return [Chapter(i, f"Titulo {i}", volume="1") for i in range(n)]


def run(n, failing=(), existing=()):
    events, fetched, lock = [], [], threading.Lock()

    def fetch(chapter):
        time.sleep(0.005)
        with lock:
            events.append(("fetch", chapter.number))
            fetched.append(chapter.number)
        return (False, "sem token") if chapter.number in failing else (True, None)

    audio = FakeAudio(events, fetched, lock, existing)
    result = run_chapters(
        chapters(n), "Teste", {"cbz", "audio"}, fetch=fetch, audio=audio, lookahead=LOOKAHEAD, delay_s=0
    )
    return result, events, audio


def test_audio_starts_right_after_the_first_download():
    _, events, _ = run(15)
    assert events.index(("audio", 0)) < events.index(("fetch", 6))


def test_never_fetches_more_than_the_lookahead():
    result, _, audio = run(30)
    assert result["successes"] == 30
    assert audio.max_ahead <= LOOKAHEAD
    assert audio.closed


def test_audio_keeps_chapter_order():
    _, events, _ = run(12)
    order = [n for kind, n in events if kind == "audio"]
    assert order == sorted(order)


def test_failed_download_skips_audio_without_hanging():
    result, events, _ = run(10, failing={3, 4})
    assert result["successes"] == 8
    assert [f["chapter"].number for f in result["failures"]] == [3, 4]
    assert ("audio", 3) not in events and ("audio", 4) not in events


def test_progress_lines(capsys):
    run(4, existing={2})
    out = capsys.readouterr().out
    assert "Teste | 4 capitulo(s) | saidas: CBZ + Audiolivro" in out
    assert "[Cap 0 audio] comecando o audio (item 1 de 4 da lista)" in out
    assert "[Cap 0 OK] 1/4 concluidos, restam 3 | Vol. 1 - Titulo 0 | faltam 2 audio(s), previsao ~" in out
    assert "[Cap 2 audio] ja existe no servidor, pulando" in out
    assert "[Cap 3 OK] 4/4 concluidos, restam 0" in out


def test_failure_line_shows_reason(capsys):
    run(1, failing={0})
    assert "[Cap 0 ERRO] 1/1 concluidos, restam 0 | Vol. 1 - Titulo 0 | motivo: sem token" in capsys.readouterr().out


def test_without_audio_fetches_in_parallel():
    seen = []
    result = run_chapters(
        chapters(5), "Teste", {"cbz"}, fetch=lambda c: seen.append(c.number) or (True, None), delay_s=0
    )
    assert result["successes"] == 5 and sorted(seen) == [0, 1, 2, 3, 4]


class InterruptedAudio(FakeAudio):
    def generate_chapter(self, chapter):
        if chapter.number == 2:
            raise Interrupted()
        return super().generate_chapter(chapter)


def test_interrupted_audio_returns_the_rest_as_stopped():
    events, fetched, lock = [], [], threading.Lock()
    audio = InterruptedAudio(events, fetched, lock)
    stop = threading.Event()
    result = run_chapters(chapters(6), "Teste", {"audio"}, audio=audio, lookahead=LOOKAHEAD, delay_s=0, stop=stop)
    assert result["successes"] == 2 and result["failures"] == []
    assert [c.number for c in result["stopped"]] == [2, 3, 4, 5]
    assert audio.closed


def test_stop_event_halts_downloads():
    stop = threading.Event()
    seen = []

    def fetch(chapter):
        seen.append(chapter.number)
        if chapter.number == 1:
            stop.set()
        return True, None

    result = run_chapters(chapters(10), "Teste", {"cbz"}, fetch=fetch, parallel=1, delay_s=0.01, stop=stop)
    assert len(result["stopped"]) >= 7 and result["successes"] + len(result["stopped"]) == 10
