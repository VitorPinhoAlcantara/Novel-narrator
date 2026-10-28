import os
import tempfile
import zipfile
from pathlib import Path

import pymupdf
from PIL import Image


def pdf_to_images(pdf_path, output_folder, dpi=150, jpg_quality=95):
    paths = []
    with pymupdf.open(pdf_path) as doc:
        digits = len(str(doc.page_count))
        zoom = pymupdf.Matrix(dpi / 72, dpi / 72)
        for index in range(doc.page_count):
            pix = doc.load_page(index).get_pixmap(matrix=zoom)
            image = Image.frombytes("RGBA" if pix.alpha else "RGB", [pix.width, pix.height], pix.samples)
            path = os.path.join(output_folder, f"{str(index + 1).zfill(digits)}.jpg")
            image.convert("RGB").save(path, "JPEG", quality=jpg_quality, optimize=True)
            paths.append(path)
    return paths


def pdf_to_cbz(pdf_path, output_folder=None, overwrite=False, dpi=150, jpg_quality=95):
    pdf_path = Path(pdf_path)
    if not pdf_path.is_file() or pdf_path.suffix.lower() != ".pdf":
        raise ValueError(f"PDF nao encontrado: {pdf_path}")
    folder = Path(output_folder) if output_folder else pdf_path.parent
    folder.mkdir(parents=True, exist_ok=True)
    cbz_path = folder / f"{pdf_path.stem}.cbz"
    if cbz_path.exists() and not overwrite:
        return None
    partial = cbz_path.with_name(cbz_path.name + ".part")
    try:
        with tempfile.TemporaryDirectory(prefix="pdf2cbz_") as temp:
            images = pdf_to_images(pdf_path, temp, dpi, jpg_quality)
            if not images:
                raise ValueError(f"nenhuma pagina em {pdf_path.name}")
            with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED) as cbz:
                for image in images:
                    cbz.write(image, os.path.basename(image))
        os.replace(partial, cbz_path)
    finally:
        if partial.exists():
            partial.unlink()
    return str(cbz_path)


def convert_folder(folder, output_folder=None, recursive=False, overwrite=False, dpi=150, jpg_quality=95):
    pdfs = sorted(Path(folder).rglob("*.pdf") if recursive else Path(folder).glob("*.pdf"))
    converted, failed = [], []
    for pdf in pdfs:
        try:
            if pdf_to_cbz(pdf, output_folder, overwrite, dpi, jpg_quality):
                converted.append(pdf)
        except Exception as exc:
            failed.append((pdf, str(exc)))
    return converted, failed
