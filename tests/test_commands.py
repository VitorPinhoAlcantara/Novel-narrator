from unittest import mock

import pytest

from novel_narrator.cli import commands
from novel_narrator.models import Chapter, Novel

CHAPTERS = [Chapter(1, "Sumario", suggested=False), Chapter(2, "Um"), Chapter(3, "Dois", volume="2")]
OK = {"successes": 1, "failures": [], "stopped": []}


def book(argv):
    source = mock.Mock(**{"novel.return_value": Novel("Livro"), "chapters.return_value": CHAPTERS})
    session = mock.Mock(**{"prepare.return_value": None})
    with (
        mock.patch.object(commands, "EpubSource", return_value=source),
        mock.patch.object(commands, "AudiobookSession", return_value=session),
        mock.patch.object(commands.abs_api, "library_destination", return_value={"library": "Livros"}) as library,
        mock.patch.object(commands, "run_chapters", return_value=OK) as run,
    ):
        code = commands.run(["book", "x.pdf", *argv])
    return code, run, library


def test_book_defaults_to_suggested_chapters():
    code, run, library = book([])
    assert code == 0 and [c.number for c in run.call_args.args[0]] == [2, 3]
    library.assert_called_once_with(mock.ANY, None)


def test_book_with_explicit_chapters_and_library():
    code, run, library = book(["--chapters", "1-2", "--library", "Livros", "--overwrite"])
    assert [c.number for c in run.call_args.args[0]] == [1, 2] and run.call_args.kwargs["overwrite"]
    assert library.call_args.args[1] == "Livros"


def test_book_list_does_not_run():
    code, run, _ = book(["--list"])
    assert code == 0 and not run.called


def test_novel_selects_by_volume_and_reports_failures(capsys):
    source = mock.Mock(
        **{"parse_url.return_value": "u", "novel.return_value": Novel("N"), "chapters.return_value": CHAPTERS}
    )
    failed = {"successes": 0, "failures": [{"chapter": CHAPTERS[2]}], "stopped": []}
    with (
        mock.patch.object(commands, "CentralNovel", return_value=source),
        mock.patch.object(commands, "run_chapters", return_value=failed) as run,
    ):
        code = commands.run(["novel", "u", "--volumes", "2", "--outputs", "pdf"])
    assert code == 1 and [c.number for c in run.call_args.args[0]] == [3] and run.call_args.args[2] == {"pdf"}
    assert "Com erro" in capsys.readouterr().out


@pytest.mark.parametrize(
    "argv", [["novel", "u"], ["novel", "u", "--chapters", "9"], ["novel", "u", "--outputs", "mp3"]]
)
def test_novel_bad_selection_is_an_error(argv):
    source = mock.Mock(
        **{"parse_url.return_value": "u", "novel.return_value": Novel("N"), "chapters.return_value": CHAPTERS}
    )
    with mock.patch.object(commands, "CentralNovel", return_value=source):
        assert commands.run(argv) == 1
