import os
import time
from pathlib import Path
from threading import Lock

import pymupdf
import requests

from novel_narrator.log import log
from novel_narrator.sources.base import SourceError
from novel_narrator.sources.centralnovel import has_site_link, is_translation_mark, pdf_page_url

_FITZ_LOCK = Lock()
_MIN_PDF_BYTES = 1000


def download_pdf(source, chapter, destination, overwrite=False, retries=3, delay_s=3, clean=None, tag="PDF"):
    if os.path.exists(destination) and not overwrite:
        log(tag, "PDF ja existia, nada a baixar")
        return True, None
    partial = f"{destination}.part"
    try:
        for attempt in range(1, retries + 1):
            try:
                url = source.pdf_url(chapter)
                time.sleep(0.5)
                response = source.session.get(url, headers={"Referer": pdf_page_url(chapter)}, timeout=60, stream=True)
                response.raise_for_status()
                break
            except requests.HTTPError as exc:
                if exc.response is None or exc.response.status_code != 429 or attempt == retries:
                    return False, f"Erro HTTP: {exc}"
                wait = delay_s * attempt * 2
                log(
                    tag,
                    f"AVISO: o site limitou os pedidos (erro 429); aguardando {wait}s "
                    f"e tentando de novo (tentativa {attempt + 1} de {retries})",
                )
                time.sleep(wait)

        os.makedirs(os.path.dirname(os.path.abspath(destination)), exist_ok=True)
        with open(partial, "wb") as file:
            for chunk in response.iter_content(chunk_size=8192):
                file.write(chunk)
        if os.path.getsize(partial) < _MIN_PDF_BYTES:
            return False, "Arquivo baixado muito pequeno (possivelmente corrompido)"
        if clean:
            try:
                clean_pdf(partial, **clean)
            except Exception as exc:
                log(tag, f"AVISO: nao foi possivel limpar o PDF: {exc}")
        os.replace(partial, destination)
        log(tag, f"PDF baixado ({os.path.getsize(destination) / 1024:.0f} KB)")
        return True, None
    except SourceError as exc:
        return False, f"Nao foi possivel obter o link do PDF: {exc}"
    except (requests.RequestException, OSError) as exc:
        return False, f"Erro: {exc}"
    finally:
        if os.path.exists(partial):
            os.remove(partial)


def clean_pdf(path, remove_site_link=True, remove_translation_mark=True):
    if not (remove_site_link or remove_translation_mark):
        return False
    partial = f"{path}.clean"
    with _FITZ_LOCK:
        try:
            with pymupdf.open(path) as doc:
                if doc.page_count == 0 or not _clean_page(doc[0], remove_site_link, remove_translation_mark):
                    return False
                doc.save(partial, garbage=3, deflate=True)
            os.replace(partial, path)
            return True
        finally:
            if os.path.exists(partial):
                os.remove(partial)


def clean_folder(folder, recursive=True, **options):
    pdfs = sorted(Path(folder).glob("**/*.pdf" if recursive else "*.pdf"))
    changed = failed = 0
    for pdf in pdfs:
        try:
            changed += clean_pdf(str(pdf), **options)
        except Exception as exc:
            failed += 1
            print(f"[ERRO] {pdf.name}: {exc}")
    return changed, failed, len(pdfs)


def _clean_page(page, remove_site_link, remove_translation_mark):
    areas = []
    if remove_site_link:
        areas.extend(_link_areas(page))
    if remove_translation_mark:
        areas.extend(_mark_areas(page))
    if not areas:
        return False
    for area in areas:
        page.add_redact_annot(area, fill=(1, 1, 1))
    page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE, graphics=pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_TOUCHED)
    return True


def _link_areas(page):
    return [
        pymupdf.Rect(span["bbox"])
        for block in page.get_text("dict")["blocks"]
        for line in block.get("lines", [])
        for span in line["spans"]
        if has_site_link(span["text"])
    ]


def _mark_areas(page):
    blocks = [b for b in page.get_text("blocks") if b[6] == 0]
    marks = [b for b in blocks if is_translation_mark(b[4])]
    areas = []
    for mark in marks:
        below = [b[1] for b in blocks if b not in marks and b[1] >= mark[3] - 0.5]
        limit = min(below, default=mark[3]) - 2
        areas.append(pymupdf.Rect(0, mark[1] - 1, page.rect.width, max(mark[3], limit)))
    return areas
