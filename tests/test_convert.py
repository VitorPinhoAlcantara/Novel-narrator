import zipfile

import pymupdf

from novel_narrator.sources.convert import is_book, to_epub
from novel_narrator.sources.epub import EpubSource

LONG = "Texto do capitulo que fala sobre a Lua e as mares. " * 30


def make_pdf(path, toc=True):
    document = pymupdf.open()
    for text in ["Capa", LONG, LONG, LONG]:
        page = document.new_page()
        page.insert_textbox(pymupdf.Rect(50, 50, 550, 750), text)
        page.insert_text((300, 800), "7")
    if toc:
        document.set_toc([[1, "Capitulo 1", 2], [1, "Capitulo 2", 4]])
    document.set_metadata({"title": "O livro da Lua", "author": "Autora Teste"})
    document.save(path)


def make_docx(path):
    def p(text, style=None):
        props = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
        return f"<w:p>{props}<w:r><w:t>{text}</w:t></w:r></w:p>"

    body = p("Prefacio curto") + p("Capitulo 1", "Ttulo1") + p(LONG) + p("Parte", "Ttulo2") + p(LONG)
    body += p("Capitulo 2", "Heading1") + p(LONG)
    ns = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    core = (
        '<cp:coreProperties xmlns:cp="x" xmlns:dc="http://purl.org/dc/elements/1.1/">'
        "<dc:title>Doc da Lua</dc:title><dc:creator>Autor Docx</dc:creator></cp:coreProperties>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", f"<w:document {ns}><w:body>{body}</w:body></w:document>")
        archive.writestr("docProps/core.xml", core)


def test_pdf_chapters_follow_the_toc(tmp_path):
    make_pdf(tmp_path / "lua.pdf")
    source = EpubSource(tmp_path / "lua.pdf")
    assert source.novel().title == "O livro da Lua" and source.novel().author == "Autora Teste"
    assert [c.title for c in source.chapters()] == ["Inicio", "Capitulo 1", "Capitulo 2"]
    second = source.chapters()[1]
    assert second.chars > 2 * len(LONG) - 100 and "7" not in second.paragraphs
    assert source.novel().url.endswith(".epub")


def test_pdf_without_toc_is_split_in_page_parts(tmp_path):
    make_pdf(tmp_path / "lua.pdf", toc=False)
    assert [c.title for c in EpubSource(tmp_path / "lua.pdf").chapters()] == ["Paginas 1-4"]


def test_docx_splits_on_top_heading(tmp_path):
    make_docx(tmp_path / "lua.docx")
    source = EpubSource(tmp_path / "lua.docx")
    assert source.novel().title == "Doc da Lua" and source.novel().author == "Autor Docx"
    chapters = source.chapters()
    assert [c.title for c in chapters] == ["Inicio", "Capitulo 1", "Capitulo 2"]
    assert "Parte" in chapters[1].paragraphs and not chapters[0].suggested


def test_is_book_and_epub_passthrough(tmp_path):
    epub = tmp_path / "a.EPUB"
    epub.write_bytes(b"x")
    (tmp_path / "a.txt").write_bytes(b"x")
    assert is_book(str(epub)) and not is_book(str(tmp_path / "a.txt")) and not is_book(str(tmp_path / "b.pdf"))
    assert to_epub(epub) == str(epub)
