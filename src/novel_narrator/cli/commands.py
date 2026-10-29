import argparse
import copy
import sys

import requests

from novel_narrator.cli.menus import parse_numbers
from novel_narrator.cli.panel import Panel
from novel_narrator.config import load_config
from novel_narrator.log import format_duration, summarize_chapters
from novel_narrator.outputs.files import fetch_chapter_files, needs_files
from novel_narrator.pipeline.audiobook import AudiobookSession
from novel_narrator.pipeline.runner import run_chapters
from novel_narrator.publish import abs_api
from novel_narrator.sources.base import SourceError
from novel_narrator.sources.centralnovel import CentralNovel
from novel_narrator.sources.epub import EpubSource, estimated_duration_s

OUTPUTS = ("audio", "pdf", "cbz")


def parser():
    root = argparse.ArgumentParser(
        prog="novel-narrator", description="Sem subcomando abre o menu interativo. Com subcomando roda sem perguntas."
    )
    commands = root.add_subparsers(dest="command")

    book = commands.add_parser("book", help="Audiolivro de um arquivo local (.epub, .pdf ou .docx)")
    book.add_argument("path")
    book.add_argument("--chapters", help="ex.: 1,3,10-15 ou all (padrao: os sugeridos)")
    book.add_argument("--library", help="Biblioteca do Audiobookshelf (padrao: a configurada)")

    novel = commands.add_parser("novel", help="Baixar/narrar uma novel da CentralNovel")
    novel.add_argument("novel", help="Link da novel ou nome para buscar (usa o primeiro resultado)")
    novel.add_argument("--chapters", help="ex.: 1,3,10-15 ou all")
    novel.add_argument("--volumes", help="ex.: 1,2")
    novel.add_argument(
        "--outputs", default="audio", help=f"Separadas por virgula: {', '.join(OUTPUTS)} (padrao: audio)"
    )

    for command in (book, novel):
        command.add_argument("--list", action="store_true", help="So lista os capitulos e sai")
        command.add_argument("--overwrite", action="store_true", help="Refaz o que ja existe")

    search = commands.add_parser("search", help="Buscar novels na CentralNovel")
    search.add_argument("query")
    return root


def run(argv):
    sys.stdout.reconfigure(encoding="utf-8")  # piped output on Windows defaults to cp1252
    args = parser().parse_args(argv)
    try:
        if args.command == "book":
            return run_book(args)
        if args.command == "novel":
            return run_novel(args)
        return run_search(args)
    except (SourceError, ValueError, OSError, RuntimeError, requests.RequestException) as exc:
        print(f"[ERRO] {exc}")
        return 1


def run_search(args):
    for novel in CentralNovel().search(args.query, limit=20):
        print(f"{novel.title}\t{novel.url}")
    return 0


def run_book(args):
    config = load_config()
    source = EpubSource(args.path)
    novel, chapters = source.novel(), source.chapters()
    if not chapters:
        raise ValueError("Nenhum texto encontrado nesse arquivo")
    cover = "sim" if source.cover() else "nao"
    print(f"Livro: {novel.title} | Autor: {novel.author or '(nao informado)'} | Capa: {cover}")
    if args.list:
        return list_chapters(chapters, book=True)
    selected = [c for c in chapters if c.suggested] if not args.chapters else pick(chapters, args.chapters)

    run_config = copy.deepcopy(config)
    run_config["abs"].update(abs_api.library_destination(config["abs"], args.library))
    session = AudiobookSession(source, novel, run_config, book_mode=True)
    error = session.prepare()
    if error:
        raise ValueError(error)
    estimate = format_duration(estimated_duration_s(selected))
    print(f"Biblioteca: {run_config['abs']['library']} | capitulos: {len(selected)} | audio estimado: ~{estimate}")
    with Panel():
        result = run_chapters(selected, novel.title, {"audio"}, audio=session, overwrite=args.overwrite)
    return report(result)


def run_novel(args):
    config = load_config()
    source = CentralNovel(retries=config["download"]["max_retries"])
    url = source.parse_url(args.novel)
    if not url:
        found = source.search(args.novel, limit=1)
        if not found:
            raise ValueError(f"Nenhuma novel encontrada para: {args.novel}")
        url = found[0].url
    novel = source.novel(url)
    chapters = source.chapters(novel)
    print(f"Novel: {novel.title} | {url} | {len(chapters)} capitulos")
    if args.list:
        return list_chapters(chapters)

    outputs = {o.strip() for o in args.outputs.split(",") if o.strip()}
    if not outputs or outputs - set(OUTPUTS):
        raise ValueError(f"Saidas invalidas: {args.outputs} (use {', '.join(OUTPUTS)})")
    volumes = {v.strip() for v in (args.volumes or "").split(",") if v.strip()}
    selected = pick(chapters, args.chapters) if args.chapters else []
    selected += [c for c in chapters if c.volume in volumes and c not in selected]
    if not selected:
        raise ValueError("Informe --chapters ou --volumes (use --list para ver os capitulos)")

    session = None
    if "audio" in outputs:
        session = AudiobookSession(source, novel, config)
        error = session.prepare()
        if error:
            raise ValueError(error)
    download = config["download"]
    with Panel():
        result = run_chapters(
            selected,
            novel.title,
            outputs,
            fetch=lambda c: fetch_chapter_files(source, config, novel.title, c, outputs, args.overwrite),
            audio=session,
            overwrite=args.overwrite,
            parallel=download["parallel"],
            lookahead=download["lookahead_chapters"],
            needs_download=lambda c: needs_files(config, novel.title, c, outputs, args.overwrite),
        )
    return report(result)


def pick(chapters, text):
    if text.strip().lower() == "all":
        return list(chapters)
    wanted = set(parse_numbers(text))
    selected = [c for c in chapters if c.number in wanted]
    if not selected:
        raise ValueError(f"Nenhum capitulo com os numeros: {text}")
    return selected


def list_chapters(chapters, book=False):
    for c in chapters:
        if book:
            mark = "*" if c.suggested else " "
            print(f"{mark} {c.number:>4}  {c.title[:70]}  (~{format_duration(estimated_duration_s([c]))})")
        else:
            print(f"{c.number:>5}  vol {c.volume:<4} {c.title[:70]}")
    if book:
        print("* = sugerido (usado quando --chapters nao e informado)")
    return 0


def report(result):
    failures = result["failures"]
    print(f"\nConcluido: {result['successes']} ok, {len(failures)} com erro")
    if failures:
        print(f"Com erro: {summarize_chapters([f['chapter'] for f in failures])}")
    return 1 if failures or result.get("stopped") else 0
