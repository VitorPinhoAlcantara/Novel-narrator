import io

from novel_narrator.cli.panel import Panel, footer
from novel_narrator.models import Chapter
from novel_narrator.pipeline import progress
from novel_narrator.pipeline.progress import Tracker, emit
from novel_narrator.pipeline.runner import run_chapters

CHAPTERS = [Chapter(n, f"Titulo {n}", volume="1") for n in range(1, 5)]


class FakeAudio:
    def pending(self, chapter, overwrite=False):
        return chapter.number in (3, 4)

    def generate_chapter(self, chapter):
        emit("segments", ids=["0000", "0001"], done=["0000"])
        emit("segment_start", id="0001", text="Sunny olhou para a névoa.", worker=0)
        snapshots.append(tracker.snapshot())
        emit("segment_done", id="0001", ok=True)
        emit("audio_done", chapter=chapter, minutes=2.0, seconds=30, alerts=1 if chapter.number == 4 else 0)
        return True, None

    def close(self):
        pass


tracker = Tracker()
snapshots = []


def run(**kwargs):
    progress.add_listener(tracker)
    try:
        return run_chapters(
            CHAPTERS,
            "Teste",
            {"cbz", "audio"},
            audio=FakeAudio(),
            delay_s=0,
            needs_download=lambda c: c.number in (2, 3),
            **kwargs,
        )
    finally:
        progress.remove_listener(tracker)


def test_plan_skips_complete_chapters_and_tracks_segments():
    done = []
    result = run(on_done=lambda d, t: done.append((d, t)))
    assert result["successes"] == 4 and done[0] == (1, 4)
    current = snapshots[-1]["current"]
    assert current["active"] == ["Sunny olhou para a névoa."]
    assert current["segments"] == ["d", "a"]
    final = tracker.snapshot()
    assert (final["skipped"], final["dl_total"], final["audio_total"], final["warned"]) == (1, 2, 2, 1)
    assert final["current"] is None and final["done"] == 4
    tiles = [e["tile"] for e in final["volumes"][0][1]]
    assert tiles == ["skip", "ok", "ok", "warn"]


def test_footer_shows_the_phrase_being_converted():
    snapshot = snapshots[-1]
    rows = footer(snapshot, 60)
    assert any("Sunny olhou para a névoa." in row for row in rows)
    assert all(len(row.replace("\x1b[0m", "")) < 120 for row in rows)


def test_panel_is_off_when_output_is_not_a_terminal():
    with Panel(stream=io.StringIO()) as panel:
        assert not panel.active
