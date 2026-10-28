from pathlib import Path

import requests
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from novel_narrator.cli.menus import list_volumes, parse_numbers
from novel_narrator.config import load_config
from novel_narrator.log import format_duration
from novel_narrator.outputs.files import fetch_chapter_files
from novel_narrator.pipeline.audiobook import AudiobookSession
from novel_narrator.pipeline.runner import OUTPUT_NAMES, describe_outputs, run_chapters
from novel_narrator.publish import abs_api, abs_server
from novel_narrator.sources.base import SourceError
from novel_narrator.sources.centralnovel import CentralNovel
from novel_narrator.sources.epub import EpubSource, estimated_duration_s
from novel_narrator.web.jobs import JobQueue

STATUS_NAMES = {"queued": "na fila", "running": "rodando", "done": "concluido", "failed": "com falhas"}


def create_app(config=None, source=None, jobs=None):
    config = config or load_config()
    source = source or CentralNovel(retries=config["download"]["max_retries"])
    jobs = jobs or JobQueue()
    novels = {}
    templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
    templates.env.globals.update(
        output_names=OUTPUT_NAMES,
        status_names=STATUS_NAMES,
        describe_outputs=describe_outputs,
        format_duration=format_duration,
    )
    app = FastAPI(title="Novel Narrator")

    def page(request, name, **context):
        return templates.TemplateResponse(request, name, context)

    def load_novel(url):
        if url not in novels:
            try:
                novel = source.novel(url)
                novels[url] = (novel, source.chapters(novel))
            except SourceError as exc:
                raise HTTPException(502, f"Nao foi possivel carregar a novel: {exc}") from exc
        return novels[url]

    def novel_task(task_source, novel, outputs, overwrite, run_config=config, book_mode=False):
        download = run_config["download"]

        def run(job):
            audio = (
                AudiobookSession(task_source, novel, run_config, book_mode=book_mode) if "audio" in outputs else None
            )
            return run_chapters(
                job.chapters,
                novel.title,
                outputs,
                fetch=lambda c: fetch_chapter_files(task_source, run_config, novel.title, c, outputs, overwrite),
                audio=audio,
                overwrite=overwrite,
                parallel=download["parallel"],
                lookahead=download["lookahead_chapters"],
                on_done=job.progress,
            )

        return run

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request, q: str = ""):
        error = ""
        try:
            novels_found = source.search(q, limit=20) if q.strip() else source.popular(limit=12)
        except SourceError as exc:
            novels_found, error = [], str(exc)
        return page(request, "index.html", query=q, novels=novels_found, error=error, jobs=jobs.recent()[:5])

    @app.get("/novel", response_class=HTMLResponse)
    def novel_page(request: Request, url: str):
        parsed = source.parse_url(url)
        if not parsed:
            raise HTTPException(400, "Link de novel invalido")
        novel, chapters = load_novel(parsed)
        volumes = []
        for name in list_volumes(chapters):
            numbers = [c.number for c in chapters if c.volume == name]
            volumes.append({"name": name, "count": len(numbers), "first": min(numbers), "last": max(numbers)})
        defaults = {"audio" if k == "audiobook" else k for k, on in config["outputs"].items() if on}
        return page(request, "novel.html", novel=novel, chapters=chapters, volumes=volumes, defaults=defaults)

    @app.post("/novel/run")
    async def novel_run(request: Request):
        form = await request.form()
        novel, chapters = load_novel(form["url"])
        volumes = set(form.getlist("volumes"))
        try:
            numbers = set(parse_numbers(form.get("numbers", "")))
        except ValueError as exc:
            raise HTTPException(400, "Formato de capitulos invalido (use 1,2,10-15)") from exc
        selected = [c for c in chapters if c.volume in volumes or c.number in numbers]
        outputs = set(form.getlist("outputs"))
        if not selected or not outputs:
            raise HTTPException(400, "Escolha pelo menos um capitulo e uma saida")
        overwrite = form.get("overwrite") == "on"
        job = jobs.submit(novel.title, selected, outputs, novel_task(source, novel, outputs, overwrite))
        return RedirectResponse(f"/jobs/{job.id}", status_code=303)

    @app.get("/epub", response_class=HTMLResponse)
    def epub_page(request: Request, path: str = ""):
        book, error, libraries = None, "", []
        if path:
            try:
                book = EpubSource(path.strip().strip("\"'"))
            except Exception as exc:
                error = f"Nao foi possivel ler o EPUB: {exc}"
            try:
                libraries = abs_api.list_libraries(config["abs"])
            except requests.RequestException as exc:
                error = error or f"Nao foi possivel listar as bibliotecas do Audiobookshelf: {exc}"
        return page(
            request,
            "epub.html",
            path=path,
            book=book,
            error=error,
            libraries=libraries,
            default_library=config["abs"]["library"],
            estimate=estimated_duration_s,
        )

    @app.post("/epub/run")
    async def epub_run(request: Request):
        form = await request.form()
        book = EpubSource(form["path"])
        wanted = {int(n) for n in form.getlist("chapters")}
        selected = [c for c in book.chapters() if c.number in wanted]
        if not selected:
            raise HTTPException(400, "Escolha pelo menos um capitulo")
        run_config = {**config, "abs": {**config["abs"], **library_destination(form.get("library", ""))}}
        task = novel_task(book, book.novel(), {"audio"}, form.get("overwrite") == "on", run_config, book_mode=True)
        job = jobs.submit(book.novel().title, selected, {"audio"}, task)
        return RedirectResponse(f"/jobs/{job.id}", status_code=303)

    def library_destination(name):
        if not name:
            return {}
        libraries = abs_api.list_libraries(config["abs"])
        chosen = next((lib for lib in libraries if lib["name"] == name), None)
        if chosen is None or not chosen["folders"]:
            raise HTTPException(400, f"Biblioteca sem pasta configurada: {name}")
        changes = {"library": name}
        if abs_server.is_remote(config["abs"]):
            folder = abs_api.server_folder(chosen["folders"][0], abs_api.detect_mounts(config["abs"]))
            if folder is None:
                raise HTTPException(400, f"Nao consegui descobrir a pasta de {name} no servidor")
            changes["remote_dir"] = folder
        return changes

    @app.get("/jobs", response_class=HTMLResponse)
    def jobs_page(request: Request):
        return page(request, "jobs.html", jobs=jobs.recent())

    @app.get("/jobs/{job_id}", response_class=HTMLResponse)
    def job_page(request: Request, job_id: int):
        return page(request, "job.html", job=find_job(job_id))

    @app.get("/jobs/{job_id}/status", response_class=HTMLResponse)
    def job_status(request: Request, job_id: int):
        return page(request, "_job_status.html", job=find_job(job_id))

    @app.post("/jobs/{job_id}/retry")
    def job_retry(job_id: int):
        job = find_job(job_id)
        if job.active or not job.failures:
            raise HTTPException(400, "Nada para tentar de novo")
        return RedirectResponse(f"/jobs/{jobs.retry(job).id}", status_code=303)

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request, test: bool = False):
        checks = []
        if test:
            try:
                libraries = abs_api.list_libraries(config["abs"])
                checks.append((True, f"API: {len(libraries)} biblioteca(s): {', '.join(x['name'] for x in libraries)}"))
            except requests.RequestException as exc:
                checks.append((False, f"API: {exc}"))
            error = abs_server.check_destination(config["abs"])
            checks.append((not error, error or "Destino acessivel"))
        return page(request, "settings.html", config=config, checks=checks)

    def find_job(job_id):
        if job_id not in jobs.jobs:
            raise HTTPException(404, "Trabalho nao encontrado")
        return jobs.jobs[job_id]

    return app
