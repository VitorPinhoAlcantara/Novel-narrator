import threading

from novel_narrator.log import chapter_tag, format_duration, log, summarize_chapters
from novel_narrator.models import Chapter


def test_chapter_tag():
    assert chapter_tag(Chapter(147, "Poder", volume="2"), "audio") == "Cap 147 audio"
    assert chapter_tag(Chapter(7, "x")) == "Cap 7"


def test_summary_of_chapters():
    assert summarize_chapters([Chapter(n, "x", volume="") for n in (1, 2, 3, 7)]) == "Caps 1-3, 7"
    assert summarize_chapters([Chapter(5, "x", volume="2")]) == "Vol 2: 5"


def test_format_duration():
    assert format_duration(45) == "45s"
    assert format_duration(521) == "8min41s"
    assert format_duration(8000) == "2h13min"
    assert format_duration(-3) == "0s"


def test_log_line_has_tag(capsys):
    log("Envio", "OK: 7 arquivo(s)")
    assert capsys.readouterr().out == "[Envio] OK: 7 arquivo(s)\n"


def test_lines_from_different_threads_do_not_mix(capsys):
    def write(prefix):
        for _ in range(200):
            log(prefix, "x" * 50)

    threads = [threading.Thread(target=write, args=(f"T{n}",)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 800
    assert all(line.startswith("[T") and line.endswith("x" * 50) for line in lines)
