import os
import posixpath
import re
import warnings
import zipfile

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

CARACTERES_MINIMOS = 1000
SEGUNDOS_POR_MIL_CARACTERES = 60
_BLOCOS = ["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "blockquote", "dt", "dd"]
_FORA_DO_AUDIO = re.compile(
    r"sum[aá]rio|[ií]ndice|ficha|cr[eé]ditos|\bnotas\b|dlivros|folha de rosto|copyright|direitos autorais|cat[aá]logo",
    re.IGNORECASE,
)


def ler_epub(caminho):
    with zipfile.ZipFile(caminho) as arquivo:
        container = BeautifulSoup(arquivo.read("META-INF/container.xml"), "xml")
        caminho_opf = container.find("rootfile")["full-path"]
        base = posixpath.dirname(caminho_opf)
        opf = BeautifulSoup(arquivo.read(caminho_opf), "xml")

        def interno(href, relativo_a=base):
            href = href.split("#", 1)[0]
            return posixpath.normpath(posixpath.join(relativo_a, href)) if relativo_a else posixpath.normpath(href)

        metadados = opf.find("metadata")
        manifesto = {item["id"]: item for item in opf.find_all("item") if item.get("id")}
        rotulos = _rotulos_do_sumario(arquivo, opf, manifesto, interno)

        capitulos = []
        for ref in opf.find("spine").find_all("itemref"):
            item = manifesto.get(ref.get("idref"))
            if item is None or "html" not in item.get("media-type", ""):
                continue
            documento = interno(item["href"])
            paragrafos = _paragrafos(arquivo.read(documento))
            if not paragrafos:
                continue
            titulo = rotulos.get(documento) or paragrafos[0][:80]
            if paragrafos[0].strip().lower() != titulo.strip().lower():
                paragrafos.insert(0, titulo)
            caracteres = sum(len(p) for p in paragrafos)
            capitulos.append({
                "titulo": titulo,
                "paragrafos": paragrafos,
                "chars": caracteres,
                "sugerido": caracteres >= CARACTERES_MINIMOS and not _FORA_DO_AUDIO.search(titulo),
            })
        for indice, capitulo in enumerate(capitulos, 1):
            capitulo["indice"] = indice

        return {
            "titulo": _texto(metadados.find("title")) or os.path.splitext(os.path.basename(caminho))[0],
            "autor": _texto(metadados.find("creator")),
            "idioma": _texto(metadados.find("language")) or "pt",
            "capa": _capa(arquivo, opf, manifesto, interno),
            "capitulos": capitulos,
        }


def duracao_estimada_s(capitulos):
    return sum(c["chars"] for c in capitulos) / 1000 * SEGUNDOS_POR_MIL_CARACTERES


def _texto(tag):
    return " ".join(tag.get_text().split()) if tag else ""


def _paragrafos(html):
    sopa = BeautifulSoup(html, "lxml")
    corpo = sopa.body or sopa
    for tag in corpo.find_all(["sup", "script", "style"]):
        tag.decompose()
    for quebra in corpo.find_all("br"):
        quebra.replace_with(" ")
    saida = []
    for tag in corpo.find_all(_BLOCOS):
        if tag.find(_BLOCOS):
            continue
        texto = " ".join(tag.get_text().split())
        if texto:
            saida.append(texto)
    if not saida:
        saida = [" ".join(linha.split()) for linha in corpo.get_text("\n").splitlines() if linha.strip()]
    return saida


def _rotulos_do_sumario(arquivo, opf, manifesto, interno):
    rotulos = {}
    nav = next((i for i in manifesto.values() if "nav" in (i.get("properties") or "").split()), None)
    if nav is not None:
        caminho_nav = interno(nav["href"])
        sopa = BeautifulSoup(arquivo.read(caminho_nav), "lxml")
        lista = None
        for elemento in sopa.find_all("nav"):
            if "toc" in (elemento.get("epub:type") or elemento.get("type") or ""):
                lista = elemento.find("ol")
                break
        if lista is not None:
            for li in lista.find_all("li", recursive=False):
                link = li.find("a")
                if link and link.get("href"):
                    rotulos.setdefault(interno(link["href"], posixpath.dirname(caminho_nav)), _texto(link))
        if rotulos:
            return rotulos

    id_ncx = opf.find("spine").get("toc")
    item_ncx = manifesto.get(id_ncx) if id_ncx else None
    if item_ncx is None:
        item_ncx = next((i for i in manifesto.values() if i.get("media-type") == "application/x-dtbncx+xml"), None)
    if item_ncx is None:
        return rotulos
    caminho_ncx = interno(item_ncx["href"])
    ncx = BeautifulSoup(arquivo.read(caminho_ncx), "xml")
    mapa = ncx.find("navMap")
    if mapa is None:
        return rotulos
    for ponto in mapa.find_all("navPoint", recursive=False):
        conteudo = ponto.find("content")
        rotulo = ponto.find("navLabel")
        if conteudo is not None and conteudo.get("src") and rotulo is not None:
            rotulos.setdefault(interno(conteudo["src"], posixpath.dirname(caminho_ncx)), _texto(rotulo))
    return rotulos


def _capa(arquivo, opf, manifesto, interno):
    meta = opf.find("meta", attrs={"name": "cover"})
    item = manifesto.get(meta.get("content")) if meta is not None else None
    if item is None or not item.get("media-type", "").startswith("image/"):
        item = next((i for i in manifesto.values() if "cover-image" in (i.get("properties") or "")), None)
    if item is None or not item.get("media-type", "").startswith("image/"):
        return None
    tipo = item["media-type"]
    return {"dados": arquivo.read(interno(item["href"])), "extensao": "png" if "png" in tipo else "jpg"}
