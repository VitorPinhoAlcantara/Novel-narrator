import os
import re

from novel_narrator.log import chapter_tag, log
from novel_narrator.outputs.cbz import pdf_to_cbz
from novel_narrator.outputs.pdf import download_pdf


def file_name(text):
    return " ".join(re.sub(r'[<>:"/\\|?*]', "", text).split()).replace(" ", "_")


def volume_folder(root, novel_title, volume):
    novel = " ".join(re.sub(r'[<>:"/\\|?*]', "", str(novel_title)).split()) or "Novel"
    return os.path.join(root, f"{novel} Vol {str(volume).strip()}")


def pdf_path(config, novel_title, chapter):
    name = f"Capitulo_{str(chapter.number).zfill(3)}_{file_name(chapter.title)}.pdf"
    return os.path.join(volume_folder(config["paths"]["pdf_dir"], novel_title, chapter.volume), name)


def cbz_path(config, novel_title, chapter):
    stem = os.path.splitext(os.path.basename(pdf_path(config, novel_title, chapter)))[0]
    return os.path.join(volume_folder(config["paths"]["cbz_dir"], novel_title, chapter.volume), f"{stem}.cbz")


def split_downloaded(config, novel_title, chapters, outputs):
    done, missing = [], []
    for chapter in chapters:
        complete = ("pdf" not in outputs or os.path.exists(pdf_path(config, novel_title, chapter))) and (
            "cbz" not in outputs or os.path.exists(cbz_path(config, novel_title, chapter))
        )
        (done if complete else missing).append(chapter)
    return done, missing


def fetch_chapter_files(source, config, novel_title, chapter, outputs, overwrite=False):
    pdf = pdf_path(config, novel_title, chapter)
    cbz = cbz_path(config, novel_title, chapter)
    want_pdf = "pdf" in outputs and (overwrite or not os.path.exists(pdf))
    want_cbz = "cbz" in outputs and (overwrite or not os.path.exists(cbz))
    if not (want_pdf or want_cbz):
        return True, None

    tag = chapter_tag(chapter, "download")
    pdf_existed = os.path.exists(pdf)
    download = config["download"]
    ok, reason = download_pdf(
        source,
        chapter,
        pdf,
        overwrite=overwrite,
        retries=download["max_retries"],
        delay_s=download["delay_s"],
        clean=config["pdf"],
        tag=tag,
    )
    if not ok:
        return False, reason
    if want_cbz:
        try:
            pdf_to_cbz(pdf, os.path.dirname(cbz), overwrite=True, **config["cbz"])
        except Exception as exc:
            return False, f"Falha na conversao para CBZ: {exc}"
        log(tag, "CBZ gerado")
        if "pdf" not in outputs and not pdf_existed:
            os.remove(pdf)
    return True, None
