import zipfile

from centralnovel.epub_local import duracao_estimada_s, ler_epub

CONTAINER = """<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>"""

LONGO = "Texto do capitulo que fala sobre a Lua e as marés. " * 40


def html(corpo):
    return f"<html xmlns='http://www.w3.org/1999/xhtml'><head><title>x</title></head><body>{corpo}</body></html>"


def criar_epub(caminho, epub3=False):
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
        '<dc:creator>Autora Teste</dc:creator><dc:language>pt</dc:language><meta name="cover" content="cover"/></metadata>'
        f"<manifest>{''.join(manifest)}</manifest>"
        '<spine toc="ncx"><itemref idref="sumario"/><itemref idref="img"/><itemref idref="c1"/><itemref idref="c2"/></spine>'
        "</package>"
    )
    ncx = (
        '<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/"><navMap>'
        '<navPoint id="a"><navLabel><text>Sumário</text></navLabel><content src="Text/sumario.html"/></navPoint>'
        '<navPoint id="b"><navLabel><text>Por que a Lua?</text></navLabel><content src="Text/cap1.html"/>'
        '<navPoint id="b1"><navLabel><text>Subsecao</text></navLabel><content src="Text/cap1.html#s1"/></navPoint></navPoint>'
        '<navPoint id="c"><navLabel><text>A Lua nova</text></navLabel><content src="Text/cap2.html"/></navPoint>'
        "</navMap></ncx>"
    )
    nav = (
        '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><body>'
        '<nav epub:type="toc"><ol><li><a href="Text/sumario.html">Sumário</a></li>'
        '<li><a href="Text/cap1.html">Por que a Lua? (nav)</a><ol><li><a href="Text/cap1.html#s1">Sub</a></li></ol></li>'
        '<li><a href="Text/cap2.html">A Lua nova (nav)</a></li></ol></nav></body></html>'
    )
    cap1 = html(
        "<h1>Por que a Lua?</h1><p>Porque ela é a âncora<sup><a href='#n1'>1</a></sup> do mundo.</p>"
        "<p>A Lu<span>a</span> é linda<br/>e brilha.</p>"
        "<ul><li><p>Item com paragrafo</p></li><li>Item solto</li></ul>"
        f"<p>{LONGO}</p>"
    )
    cap2 = html(f"<div>{LONGO}</div><div>Segunda linha sem paragrafo.</div>")
    with zipfile.ZipFile(caminho, "w") as z:
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


def test_metadados_capa_e_capitulos(tmp_path):
    caminho = tmp_path / "livro.epub"
    criar_epub(caminho)
    livro = ler_epub(str(caminho))
    assert livro["titulo"] == "O livro da Lua" and livro["autor"] == "Autora Teste" and livro["idioma"] == "pt"
    assert livro["capa"] == {"dados": b"\xff\xd8JPEGFALSO", "extensao": "jpg"}
    assert [c["titulo"] for c in livro["capitulos"]] == ["Sumário", "Por que a Lua?", "A Lua nova"]
    assert [c["indice"] for c in livro["capitulos"]] == [1, 2, 3]


def test_sugere_conteudo_e_deixa_sumario_de_fora(tmp_path):
    caminho = tmp_path / "livro.epub"
    criar_epub(caminho)
    sugeridos = {c["titulo"]: c["sugerido"] for c in ler_epub(str(caminho))["capitulos"]}
    assert sugeridos == {"Sumário": False, "Por que a Lua?": True, "A Lua nova": True}


def test_texto_limpo_sem_nota_e_sem_palavra_quebrada(tmp_path):
    caminho = tmp_path / "livro.epub"
    criar_epub(caminho)
    cap = ler_epub(str(caminho))["capitulos"][1]
    assert cap["paragrafos"][0] == "Por que a Lua?"
    assert cap["paragrafos"][1] == "Porque ela é a âncora do mundo."
    assert cap["paragrafos"][2] == "A Lua é linda e brilha."
    assert "Item com paragrafo" in cap["paragrafos"] and "Item solto" in cap["paragrafos"]


def test_titulo_entra_uma_vez_e_texto_sem_paragrafo_e_lido(tmp_path):
    caminho = tmp_path / "livro.epub"
    criar_epub(caminho)
    cap = ler_epub(str(caminho))["capitulos"][2]
    assert cap["paragrafos"][0] == "A Lua nova"
    assert any("Segunda linha sem paragrafo." in p for p in cap["paragrafos"])
    assert sum(1 for p in cap["paragrafos"] if p == "A Lua nova") == 1


def test_epub3_usa_o_sumario_nav(tmp_path):
    caminho = tmp_path / "livro3.epub"
    criar_epub(caminho, epub3=True)
    titulos = [c["titulo"] for c in ler_epub(str(caminho))["capitulos"]]
    assert titulos == ["Sumário", "Por que a Lua? (nav)", "A Lua nova (nav)"]


def test_duracao_estimada():
    assert duracao_estimada_s([{"chars": 1000}, {"chars": 2000}]) == 180
