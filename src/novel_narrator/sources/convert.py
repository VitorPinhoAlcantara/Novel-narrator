import os
import re
import tempfile
import zipfile

from bs4 import BeautifulSoup

from novel_narrator.outputs.epub_writer import write_volume_epub

FORMATS = (".epub", ".pdf", ".docx")
PAGES_PER_PART = 20
_HEADING_STYLE = re.compile(r"^(heading|t[ií]?tulo)\s*(\d)$", re.IGNORECASE)


def is_book(path):
    return bool(path) and path.lower().endswith(FORMATS) and os.path.isfile(path)


def to_epub(path):
    """Returns an EPUB for the file: the file itself, or a converted copy for PDF and DOCX."""
    path = str(path)
    stem, extension = os.path.splitext(os.path.basename(path))
    extension = extension.lower()
    if extension == ".epub":
        return path
    if extension == ".pdf":
        title, author, chapters = _read_pdf(path)
    elif extension == ".docx":
        title, author, chapters = _read_docx(path)
    else:
        raise ValueError(f"Formato nao suportado: {extension} (use {', '.join(FORMATS)})")
    folder = os.path.join(tempfile.gettempdir(), "novel-narrator", "converted")
    os.makedirs(folder, exist_ok=True)
    return write_volume_epub(os.path.join(folder, f"{stem}.epub"), title or stem, chapters, author=author)


def _read_pdf(path):
    import pymupdf

    flags = pymupdf.TEXTFLAGS_BLOCKS | pymupdf.TEXT_DEHYPHENATE
    with pymupdf.open(path) as document:
        pages = [[" ".join(block[4].split()) for block in page.get_text("blocks", flags=flags)] for page in document]
        # Bare page numbers in headers/footers would be read aloud.
        pages = [[text for text in blocks if text and not text.isdigit()] for blocks in pages]
        starts = [(title.strip(), page - 1) for level, title, page, *_ in document.get_toc() if level == 1 and page > 0]
        if starts:
            if starts[0][1] > 0:
                starts.insert(0, ("Inicio", 0))
        else:
            starts = [
                (f"Paginas {start + 1}-{min(start + PAGES_PER_PART, len(pages))}", start)
                for start in range(0, len(pages), PAGES_PER_PART)
            ]
        ends = [start for _, start in starts[1:]] + [len(pages)]
        chapters = [
            (title, [text for page in pages[start:end] for text in page])
            for (title, start), end in zip(starts, ends, strict=True)
        ]
        meta = document.metadata or {}
    return meta.get("title", "").strip(), meta.get("author", "").strip(), [c for c in chapters if c[1]]


def _read_docx(path):
    with zipfile.ZipFile(path) as archive:
        body = BeautifulSoup(archive.read("word/document.xml"), "xml").find("body")
        has_core = "docProps/core.xml" in archive.namelist()
        core = BeautifulSoup(archive.read("docProps/core.xml"), "xml") if has_core else None

    paragraphs = []
    for p in body.find_all("p"):
        text = " ".join("".join(t.get_text() if t.name == "t" else " " for t in p.find_all(["t", "tab", "br"])).split())
        if not text:
            continue
        style = p.find("pStyle")
        outline = p.find("outlineLvl")
        level = None
        if style is not None and (match := _HEADING_STYLE.match(style.get("w:val", style.get("val", "")))):
            level = int(match.group(2))
        elif outline is not None:
            level = int(outline.get("w:val", outline.get("val", "9"))) + 1
        paragraphs.append((level, text))

    levels = [level for level, _ in paragraphs if level]
    chapter_level = min(levels) if levels else None
    chapters = []
    for level, text in paragraphs:
        if level == chapter_level and level is not None:
            chapters.append((text, []))
        elif chapters:
            chapters[-1][1].append(text)
        else:
            chapters.append(("Inicio", [text]))

    def meta(name):
        tag = core.find(name) if core is not None else None
        return " ".join(tag.get_text().split()) if tag else ""

    return meta("title"), meta("creator"), [c for c in chapters if c[1]]
