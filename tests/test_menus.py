import copy
import os
from unittest import mock

import pytest

from novel_narrator.cli import menus
from novel_narrator.config import DEFAULTS
from novel_narrator.models import Chapter, Novel

NOVEL = Novel("Shadow Slave", "https://centralnovel.com/series/shadow-slave/")
OTHER = Novel("Outra Novel", "https://centralnovel.com/series/outra/")
CHAPTER = Chapter(1, "A", volume="1", url="u")


def answers(values):
    queue = iter(values)
    return lambda *args, **kwargs: mock.Mock(execute=mock.Mock(return_value=next(queue)))


def run_novel_menu(novels, selections, confirms=()):
    source = mock.Mock()
    source.novel.side_effect = lambda url: NOVEL
    source.chapters.return_value = [CHAPTER]
    with (
        mock.patch.object(menus, "clear_screen"),
        mock.patch.object(menus, "CentralNovel", return_value=source),
        mock.patch.object(menus, "load_config", return_value=copy.deepcopy(DEFAULTS)),
        mock.patch.object(menus, "select_novel", side_effect=novels) as novel,
        mock.patch.object(menus, "select_chapters_or_volumes", side_effect=selections) as chapters,
        mock.patch.object(menus, "ask_outputs", return_value={"cbz"}) as outputs,
        mock.patch.object(menus, "resolve_existing", return_value=([CHAPTER], False)),
        mock.patch.object(menus, "run_chapters", return_value={"successes": 1, "failures": []}) as run,
        mock.patch.object(menus.inquirer, "confirm", side_effect=answers(confirms)),
    ):
        menus.novel_menu()
    return novel, chapters, outputs, run


def test_after_running_goes_back_to_chapter_selection():
    novel, chapters, outputs, run = run_novel_menu([NOVEL, None], [[CHAPTER], [CHAPTER], None], [True, True])
    assert novel.call_count == 2
    assert chapters.call_count == 3
    assert run.call_count == 2
    assert outputs.call_args_list[1].args[1] == {"cbz"}


def test_back_on_selection_allows_another_novel():
    novel, _, _, run = run_novel_menu([NOVEL, OTHER, None], [None, [CHAPTER], None], [True])
    assert novel.call_count == 3
    assert run.call_count == 1


def test_no_novel_returns_without_running():
    _, _, _, run = run_novel_menu([None], [])
    assert run.call_count == 0


def test_main_menu_has_four_options():
    captured = {}

    def fake_select(**kwargs):
        captured["names"] = [c["name"] for c in kwargs["choices"]]
        return mock.Mock(execute=mock.Mock(return_value="quit"))

    with mock.patch.object(menus, "clear_screen"), mock.patch.object(menus.inquirer, "select", side_effect=fake_select):
        menus.main_menu()
    names = captured["names"]
    assert len(names) == 4
    assert names[0].startswith("CentralNovel") and names[1].startswith("Arquivo local")
    assert names[2].startswith("Configs ABS") and names[3] == "Sair"


def test_default_outputs_come_from_config():
    captured = {}

    def fake_checkbox(**kwargs):
        captured["enabled"] = {c["value"] for c in kwargs["choices"] if c["enabled"]}
        return mock.Mock(execute=mock.Mock(return_value=["cbz"]))

    with mock.patch.object(menus.inquirer, "checkbox", side_effect=fake_checkbox):
        assert menus.ask_outputs(DEFAULTS) == {"cbz"}
    assert captured["enabled"] == {"audio", "cbz"}


def test_clean_path_accepts_what_the_terminal_pastes():
    assert menus.clean_path(r'  "C:\Livros\O Livro da Lua.epub"  ') == r"C:\Livros\O Livro da Lua.epub"
    assert menus.clean_path(r"'C:\Livros\O Livro da Lua.epub'") == r"C:\Livros\O Livro da Lua.epub"
    assert menus.clean_path(r"& 'C:\Livros\a b.epub'") == r"C:\Livros\a b.epub"
    assert menus.clean_path("   ") == ""


def test_file_picker_has_priority(tmp_path):
    epub = tmp_path / "livro.epub"
    epub.write_bytes(b"x")
    with (
        mock.patch.object(menus, "file_picker", return_value=str(epub)),
        mock.patch.object(menus.inquirer, "filepath") as field,
    ):
        assert menus.ask_epub() == str(epub)
    field.assert_not_called()


def test_without_picker_falls_back_to_drag_and_drop(tmp_path):
    epub = tmp_path / "livro com espaco.epub"
    epub.write_bytes(b"x")
    with (
        mock.patch.object(menus, "file_picker", return_value=None),
        mock.patch.object(menus.inquirer, "filepath", side_effect=answers([f'"{epub}"'])),
    ):
        assert menus.ask_epub() == str(epub)


def test_empty_field_goes_back():
    with (
        mock.patch.object(menus, "file_picker", return_value=None),
        mock.patch.object(menus.inquirer, "filepath", side_effect=answers([""])),
    ):
        assert menus.ask_epub() is None


def test_broken_picker_does_not_crash(monkeypatch):
    import tkinter

    def broken(*args, **kwargs):
        raise tkinter.TclError("sem display")

    monkeypatch.setattr(tkinter, "Tk", broken)
    assert menus.file_picker() is None


def test_all_option_returns_every_item():
    captured = {}

    def fake_checkbox(**kwargs):
        captured.update(kwargs)
        return mock.Mock(execute=mock.Mock(return_value=[menus.ALL]))

    options = [{"name": "Cap 1", "value": 1}, {"name": "Cap 2", "value": 2}]
    with mock.patch.object(menus.inquirer, "checkbox", side_effect=fake_checkbox):
        assert menus.checkbox_with_all("Capitulos", options, "[ Todos ]") == [1, 2]
    assert captured["choices"][0] == {"name": "[ Todos ]", "value": menus.ALL}
    assert "ctrl+a marca todos" in captured["message"]


def test_without_all_returns_only_marked():
    options = [{"name": n, "value": n} for n in "123"]
    with mock.patch.object(menus.inquirer, "checkbox", side_effect=answers([["2"]])):
        assert menus.checkbox_with_all("Volumes", options, "[ Todos ]") == ["2"]


def test_all_volumes_select_every_chapter():
    chapters = [Chapter(n, "x", volume=str(n)) for n in (1, 2, 3)]
    with mock.patch.object(menus.inquirer, "checkbox", side_effect=answers([[menus.ALL]])):
        assert menus.select_volumes(chapters) == chapters


def test_parse_numbers():
    assert menus.parse_numbers("1, 2,10-12, 5-3") == [1, 2, 3, 4, 5, 10, 11, 12]
    with pytest.raises(ValueError):
        menus.parse_numbers("1,a")


def test_volumes_sort_numbers_before_names():
    chapters = [Chapter(1, "x", volume=v) for v in ("10", "Extra", "2", "1")]
    assert menus.list_volumes(chapters) == ["1", "2", "10", "Extra"]


def test_nothing_existing_runs_everything(tmp_path):
    config = copy.deepcopy(DEFAULTS)
    config["paths"]["cbz_dir"] = str(tmp_path)
    chapters = [Chapter(1, "a"), Chapter(2, "b")]
    assert menus.resolve_existing(config, "N", chapters, {"cbz", "audio"}, set()) == (chapters, False)


def test_existing_chapters_offer_only_missing(tmp_path):
    config = copy.deepcopy(DEFAULTS)
    chapters = [Chapter(1, "a"), Chapter(2, "b")]
    with (
        mock.patch.object(menus, "clear_screen"),
        mock.patch.object(
            menus.inquirer, "select", side_effect=lambda **k: mock.Mock(execute=lambda: k["choices"][0]["value"])
        ),
    ):
        plan = menus.resolve_existing(config, "N", chapters, {"audio"}, {("1", 1)})
    assert plan == ([chapters[1]], False)


def test_failure_log_is_removed_once_everything_recovers(tmp_path):
    config = copy.deepcopy(DEFAULTS)
    config["paths"]["data_dir"] = str(tmp_path)
    failures = [{"chapter": Chapter(3, "Tres", volume="1", url="u3"), "reason": "sem token"}]
    logs = []

    def retry(chapters):
        logs.extend(os.listdir(tmp_path / "logs"))
        return {"failures": []}

    with mock.patch.object(menus.inquirer, "confirm", side_effect=answers([True])):
        menus.handle_failures(config, "Shadow Slave", failures, retry)
    assert len(logs) == 1 and logs[0].startswith("falhas_Shadow_Slave_")
    assert os.listdir(tmp_path / "logs") == []


def test_failure_log_content(tmp_path):
    config = copy.deepcopy(DEFAULTS)
    config["paths"]["data_dir"] = str(tmp_path)
    failures = [{"chapter": Chapter(3, "Tres", volume="1", url="u3"), "reason": "sem token"}]
    text = open(menus.write_failure_log(config, "N", failures), encoding="utf-8").read()
    assert "Vol. 1 | Cap. 3 | Tres | sem token" in text and "Para refazer (menu Capitulos especificos): 3" in text
