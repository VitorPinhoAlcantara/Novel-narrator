import zipfile

from novel_narrator.models import Chapter
from novel_narrator.sources.epub import EpubSource, estimated_duration_s

CONTAINER = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>"""

LONG_TEXT = "Texto do capitulo que fala sobre a Lua e as marés. " * 40


def html(body):
    return f"<html xmlns='http://www.w3.org/1999/xhtml'><head><title>x</title></head><body>{body}</body></html>"


def make_epub(path, epub3=False):
    manifest = [
        '<item id="cover" href="Images/capa.jpg" media-type="image/jpeg"/>',
        '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
        '<item id="sumario" href="Text/sumario.html" media-type="application/xhtml+xml"/>',
        '<item id="img" href="Text/imagem.html" media-type="application/xhtml+xml"/>',
        '<item id="c1" href="Text/cap1.html" media-type="application/xhtml+xml"/>',
        '<item id="c2" href="Text/cap2.html" media-type="application/xhtml+xml"/>',
    ]
    if epub3:
        manifest.append('<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>')
    opf = (
        '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>O livro da Lua</dc:title>'
        "<dc:creator>Autora Teste</dc:creator><dc:language>pt</dc:language>"
        '<meta name="cover" content="cover"/></metadata>'
        f"<manifest>{''.join(manifest)}</manifest>"
        '<spine toc="ncx"><itemref idref="sumario"/><itemref idref="img"/>'
        '<itemref idref="c1"/><itemref idref="c2"/></spine>'
        "</package>"
    )
    ncx = (
        '<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/"><navMap>'
        '<navPoint id="a"><navLabel><text>Sumário</text></navLabel><content src="Text/sumario.html"/></navPoint>'
        '<navPoint id="b"><navLabel><text>Por que a Lua?</text></navLabel><content src="Text/cap1.html"/>'
        '<navPoint id="b1"><navLabel><text>Subsecao</text></navLabel>'
        '<content src="Text/cap1.html#s1"/></navPoint></navPoint>'
        '<navPoint id="c"><navLabel><text>A Lua nova</text></navLabel><content src="Text/cap2.html"/></navPoint>'
        "</navMap></ncx>"
    )
    nav = (
        '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><body>'
        '<nav epub:type="toc"><ol><li><a href="Text/sumario.html">Sumário</a></li>'
        '<li><a href="Text/cap1.html">Por que a Lua? (nav)</a>'
        '<ol><li><a href="Text/cap1.html#s1">Sub</a></li></ol></li>'
        '<li><a href="Text/cap2.html">A Lua nova (nav)</a></li></ol></nav></body></html>'
    )
    cap1 = html(
        "<h1>Por que a Lua?</h1><p>Porque ela é a âncora<sup><a href='#n1'>1</a></sup> do mundo.</p>"
        "<p>A Lu<span>a</span> é linda<br/>e brilha.</p>"
        "<ul><li><p>Item com paragrafo</p></li><li>Item solto</li></ul>"
        f"<p>{LONG_TEXT}</p>"
    )
    cap2 = html(f"<div>{LONG_TEXT}</div><div>Segunda linha sem paragrafo.</div>")
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml", CONTAINER)
        z.writestr("OEBPS/content.opf", opf)
        z.writestr("OEBPS/toc.ncx", ncx)
        if epub3:
            z.writestr("OEBPS/nav.xhtml", nav)
        z.writestr("OEBPS/Images/capa.jpg", b"\xff\xd8JPEGFALSO")
        z.writestr("OEBPS/Text/sumario.html", html("<h1>Sumário</h1><p>Por que a Lua?</p><p>A Lua nova</p>"))
        z.writestr("OEBPS/Text/imagem.html", html("<img src='../Images/capa.jpg'/>"))
        z.writestr("OEBPS/Text/cap1.html", cap1)
        z.writestr("OEBPS/Text/cap2.html", cap2)


def test_metadata_cover_and_chapters(tmp_path):
    make_epub(tmp_path / "livro.epub")
    source = EpubSource(tmp_path / "livro.epub")
    novel = source.novel()
    assert (novel.title, novel.author, novel.language) == ("O livro da Lua", "Autora Teste", "pt")
    assert source.cover() == (b"\xff\xd8JPEGFALSO", "jpg")
    assert [c.title for c in source.chapters()] == ["Sumário", "Por que a Lua?", "A Lua nova"]
    assert [c.number for c in source.chapters()] == [1, 2, 3]


def test_suggests_content_and_leaves_toc_out(tmp_path):
    make_epub(tmp_path / "livro.epub")
    suggested = {c.title: c.suggested for c in EpubSource(tmp_path / "livro.epub").chapters()}
    assert suggested == {"Sumário": False, "Por que a Lua?": True, "A Lua nova": True}


def test_clean_text_without_footnote_or_broken_word(tmp_path):
    make_epub(tmp_path / "livro.epub")
    chapter = EpubSource(tmp_path / "livro.epub").chapters()[1]
    assert chapter.paragraphs[0] == "Por que a Lua?"
    assert chapter.paragraphs[1] == "Porque ela é a âncora do mundo."
    assert chapter.paragraphs[2] == "A Lua é linda e brilha."
    assert "Item com paragrafo" in chapter.paragraphs and "Item solto" in chapter.paragraphs


def test_title_appears_once_and_text_outside_paragraphs_is_read(tmp_path):
    make_epub(tmp_path / "livro.epub")
    source = EpubSource(tmp_path / "livro.epub")
    chapter = source.chapters()[2]
    assert chapter.paragraphs[0] == "A Lua nova"
    assert any("Segunda linha sem paragrafo." in p for p in chapter.paragraphs)
    assert chapter.paragraphs.count("A Lua nova") == 1
    assert source.chapter_text(chapter) == chapter.paragraphs


def test_epub3_uses_nav_toc(tmp_path):
    make_epub(tmp_path / "livro3.epub", epub3=True)
    titles = [c.title for c in EpubSource(tmp_path / "livro3.epub").chapters()]
    assert titles == ["Sumário", "Por que a Lua? (nav)", "A Lua nova (nav)"]


def test_estimated_duration():
    chapters = [Chapter(1, "a", paragraphs=["x" * 1000]), Chapter(2, "b", paragraphs=["x" * 2000])]
    assert estimated_duration_s(chapters) == 180
