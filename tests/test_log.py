import threading

from novel_narrator.log import format_duration, log


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
