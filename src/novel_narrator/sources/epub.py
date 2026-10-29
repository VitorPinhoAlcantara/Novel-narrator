import os
import posixpath
import re
import warnings
import zipfile

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

from novel_narrator.models import Chapter, Novel
from novel_narrator.sources.base import Source
from novel_narrator.sources.convert import to_epub

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

MIN_CHARS = 1000
SECONDS_PER_THOUSAND_CHARS = 60
_BLOCKS = ["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "blockquote", "dt", "dd"]
_NOT_FOR_AUDIO = re.compile(
    r"sum[aá]rio|[ií]ndice|ficha|cr[eé]ditos|\bnotas\b|dlivros|folha de rosto|copyright|direitos autorais|cat[aá]logo",
    re.IGNORECASE,
)


class EpubSource(Source):
    name = "Arquivo local"

    def __init__(self, path):
        self.path = str(path)
        self._novel, self._chapters, self._cover = read_epub(to_epub(self.path))

    def novel(self, url=None):
        return self._novel

    def chapters(self, novel=None):
        return self._chapters

    def cover(self, novel=None):
        return self._cover


def estimated_duration_s(chapters):
    return sum(c.chars for c in chapters) / 1000 * SECONDS_PER_THOUSAND_CHARS


def read_epub(path):
    with zipfile.ZipFile(path) as archive:
        container = BeautifulSoup(archive.read("META-INF/container.xml"), "xml")
        opf_path = container.find("rootfile")["full-path"]
        base = posixpath.dirname(opf_path)
        opf = BeautifulSoup(archive.read(opf_path), "xml")

        def inner(href, relative_to=base):
            href = href.split("#", 1)[0]
            return posixpath.normpath(posixpath.join(relative_to, href)) if relative_to else posixpath.normpath(href)

        metadata = opf.find("metadata")
        manifest = {item["id"]: item for item in opf.find_all("item") if item.get("id")}
        labels = _toc_labels(archive, opf, manifest, inner)

        chapters = []
        for ref in opf.find("spine").find_all("itemref"):
            item = manifest.get(ref.get("idref"))
            if item is None or "html" not in item.get("media-type", ""):
                continue
            document = inner(item["href"])
            paragraphs = _paragraphs(archive.read(document))
            if not paragraphs:
                continue
            title = labels.get(document) or paragraphs[0][:80]
            if paragraphs[0].strip().lower() != title.strip().lower():
                paragraphs.insert(0, title)
            chapter = Chapter(number=len(chapters) + 1, title=title, paragraphs=paragraphs)
            chapter.suggested = chapter.chars >= MIN_CHARS and not _NOT_FOR_AUDIO.search(title)
            chapters.append(chapter)

        novel = Novel(
            title=_text(metadata.find("title")) or os.path.splitext(os.path.basename(path))[0],
            url=str(path),
            author=_text(metadata.find("creator")),
            language=_text(metadata.find("language")) or "pt",
        )
        return novel, chapters, _cover(archive, opf, manifest, inner)


def _text(tag):
    return " ".join(tag.get_text().split()) if tag else ""


def _paragraphs(html):
    soup = BeautifulSoup(html, "lxml")
    body = soup.body or soup
    for tag in body.find_all(["sup", "script", "style"]):
        tag.decompose()
    for line_break in body.find_all("br"):
        line_break.replace_with(" ")
    paragraphs = []
    for tag in body.find_all(_BLOCKS):
        if tag.find(_BLOCKS):
            continue
        text = " ".join(tag.get_text().split())
        if text:
            paragraphs.append(text)
    if not paragraphs:
        paragraphs = [" ".join(line.split()) for line in body.get_text("\n").splitlines() if line.strip()]
    return paragraphs


def _toc_labels(archive, opf, manifest, inner):
    labels = {}
    nav = next((i for i in manifest.values() if "nav" in (i.get("properties") or "").split()), None)
    if nav is not None:
        nav_path = inner(nav["href"])
        soup = BeautifulSoup(archive.read(nav_path), "lxml")
        toc = None
        for element in soup.find_all("nav"):
            if "toc" in (element.get("epub:type") or element.get("type") or ""):
                toc = element.find("ol")
                break
        if toc is not None:
            for li in toc.find_all("li", recursive=False):
                link = li.find("a")
                if link and link.get("href"):
                    labels.setdefault(inner(link["href"], posixpath.dirname(nav_path)), _text(link))
        if labels:
            return labels

    ncx_id = opf.find("spine").get("toc")
    ncx_item = manifest.get(ncx_id) if ncx_id else None
    if ncx_item is None:
        ncx_item = next((i for i in manifest.values() if i.get("media-type") == "application/x-dtbncx+xml"), None)
    if ncx_item is None:
        return labels
    ncx_path = inner(ncx_item["href"])
    nav_map = BeautifulSoup(archive.read(ncx_path), "xml").find("navMap")
    if nav_map is None:
        return labels
    for point in nav_map.find_all("navPoint", recursive=False):
        content = point.find("content")
        label = point.find("navLabel")
        if content is not None and content.get("src") and label is not None:
            labels.setdefault(inner(content["src"], posixpath.dirname(ncx_path)), _text(label))
    return labels


def _cover(archive, opf, manifest, inner):
    meta = opf.find("meta", attrs={"name": "cover"})
    item = manifest.get(meta.get("content")) if meta is not None else None
    if item is None or not item.get("media-type", "").startswith("image/"):
        item = next((i for i in manifest.values() if "cover-image" in (i.get("properties") or "")), None)
    if item is None or not item.get("media-type", "").startswith("image/"):
        return None
    return archive.read(inner(item["href"])), "png" if "png" in item["media-type"] else "jpg"
