import copy
import os
import zipfile

import pymupdf
import pytest

from novel_narrator.config import DEFAULTS
from novel_narrator.models import Chapter
from novel_narrator.outputs import files
from novel_narrator.outputs.cbz import convert_folder, pdf_to_cbz
from novel_narrator.outputs.pdf import clean_pdf

MARK = "Traduzido usando o ChatGPT"
LINK = "https://centralnovel.com/novel-teste-capitulo-1/"
BODY = "O capitulo comeca aqui."


def make_pdf(path, pages=1, first_page_lines=(MARK, LINK, BODY)):
    with pymupdf.open() as doc:
        for index in range(pages):
            page = doc.new_page()
            lines = first_page_lines if index == 0 else (f"Pagina {index + 1}",)
            for n, line in enumerate(lines):
                page.insert_text((72, 72 + n * 40), line)
        doc.save(path)


def page_text(path):
    with pymupdf.open(path) as doc:
        return doc[0].get_text()


@pytest.fixture
def config(tmp_path):
    config = copy.deepcopy(DEFAULTS)
    config["paths"]["pdf_dir"] = str(tmp_path / "PDF")
    config["paths"]["cbz_dir"] = str(tmp_path / "CBZ")
    return config


def test_clean_removes_site_link_and_translation_mark(tmp_path):
    pdf = str(tmp_path / "cap.pdf")
    make_pdf(pdf)
    assert clean_pdf(pdf) is True
    text = page_text(pdf)
    assert BODY in text and MARK not in text and "centralnovel.com" not in text


def test_clean_leaves_pdf_without_noise_untouched(tmp_path):
    pdf = str(tmp_path / "cap.pdf")
    make_pdf(pdf, first_page_lines=(BODY,))
    before = os.path.getmtime(pdf)
    assert clean_pdf(pdf) is False
    assert os.path.getmtime(pdf) == before


def test_clean_can_keep_the_mark(tmp_path):
    pdf = str(tmp_path / "cap.pdf")
    make_pdf(pdf)
    clean_pdf(pdf, remove_translation_mark=False)
    assert MARK in page_text(pdf) and "centralnovel.com" not in page_text(pdf)


def test_pdf_becomes_cbz_with_one_image_per_page(tmp_path):
    pdf = str(tmp_path / "cap.pdf")
    make_pdf(pdf, pages=3)
    cbz = pdf_to_cbz(pdf, tmp_path / "out", dpi=40)
    with zipfile.ZipFile(cbz) as archive:
        assert archive.namelist() == ["1.jpg", "2.jpg", "3.jpg"]
    assert pdf_to_cbz(pdf, tmp_path / "out", dpi=40) is None
    assert not [n for n in os.listdir(tmp_path / "out") if n.endswith(".part")]


def test_convert_folder_reports_broken_pdfs(tmp_path):
    make_pdf(str(tmp_path / "a.pdf"))
    (tmp_path / "b.pdf").write_bytes(b"nao e pdf")
    converted, failed = convert_folder(tmp_path, tmp_path / "out", dpi=40)
    assert [p.name for p in converted] == ["a.pdf"]
    assert [p.name for p, _ in failed] == ["b.pdf"]


def test_paths_follow_novel_and_volume(config):
    chapter = Chapter(7, 'Quem? Eu: "nao"', volume="2")
    assert files.pdf_path(config, "Shadow Slave", chapter).endswith(
        os.path.join("Shadow Slave Vol 2", "Capitulo_007_Quem_Eu_nao.pdf")
    )
    assert files.cbz_path(config, "Shadow Slave", chapter).endswith(
        os.path.join("Shadow Slave Vol 2", "Capitulo_007_Quem_Eu_nao.cbz")
    )


def test_split_downloaded_checks_every_requested_output(config):
    done_chapter, missing_chapter = Chapter(1, "a"), Chapter(2, "b")
    for path in (files.pdf_path(config, "N", done_chapter), files.cbz_path(config, "N", done_chapter)):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "w").close()
    os.makedirs(os.path.dirname(files.pdf_path(config, "N", missing_chapter)), exist_ok=True)
    open(files.pdf_path(config, "N", missing_chapter), "w").close()
    done, missing = files.split_downloaded(config, "N", [done_chapter, missing_chapter], {"pdf", "cbz"})
    assert done == [done_chapter] and missing == [missing_chapter]


def test_cbz_only_keeps_no_temporary_pdf(config, monkeypatch):
    def fake_download(source, chapter, destination, **kwargs):
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        make_pdf(destination, pages=2)
        return True, None

    monkeypatch.setattr(files, "download_pdf", fake_download)
    config["cbz"]["dpi"] = 40
    chapter = Chapter(1, "Um")
    assert files.fetch_chapter_files(None, config, "N", chapter, {"cbz"}) == (True, None)
    assert os.path.exists(files.cbz_path(config, "N", chapter))
    assert not os.path.exists(files.pdf_path(config, "N", chapter))


def test_existing_outputs_are_not_downloaded_again(config, monkeypatch):
    monkeypatch.setattr(files, "download_pdf", lambda *a, **k: pytest.fail("nao deveria baixar"))
    chapter = Chapter(1, "Um")
    path = files.cbz_path(config, "N", chapter)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").close()
    assert files.fetch_chapter_files(None, config, "N", chapter, {"cbz"}) == (True, None)
