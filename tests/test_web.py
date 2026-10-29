import copy

import pytest
from fastapi.testclient import TestClient

from novel_narrator.config import DEFAULTS
from novel_narrator.log import log
from novel_narrator.models import Chapter, Novel
from novel_narrator.sources.base import Source, SourceError
from novel_narrator.web import app as web_app
from novel_narrator.web.app import create_app
from novel_narrator.web.jobs import Job, JobQueue

URL = "https://centralnovel.com/series/novel-teste/"
NOVEL = Novel("Novel Teste", URL, author="Fulano")
CHAPTERS = [Chapter(1, "Um", "1"), Chapter(2, "Dois", "1"), Chapter(3, "Tres", "2"), Chapter(0, "Extra", "Extra")]


class FakeSource(Source):
    def __init__(self, fail=False):
        self.fail = fail

    def popular(self, limit=10):
        if self.fail:
            raise SourceError("site fora do ar")
        return [NOVEL]

    def search(self, query, limit=10):
        return [NOVEL] if "teste" in query.lower() else []

    def parse_url(self, text):
        return text if text.startswith("https://centralnovel.com/series/") else None

    def novel(self, url):
        return NOVEL

    def chapters(self, novel):
        return CHAPTERS


class ManualQueue(JobQueue):
    def __init__(self):
        self.jobs = {}
        self.submitted = []
        self._ids = iter(range(1, 100))
        self._pending = Pending()

    def submit(self, title, chapters, outputs, task):
        job = Job(next(self._ids), title, list(chapters), set(outputs), task, total=len(chapters))
        self.jobs[job.id] = job
        self.submitted.append(job)
        return job


class Pending(list):
    def put(self, job):
        self.append(job)


@pytest.fixture
def queue():
    return ManualQueue()


@pytest.fixture
def client(queue):
    return TestClient(create_app(copy.deepcopy(DEFAULTS), FakeSource(), queue))


def test_home_lists_popular_novels(client):
    page = client.get("/").text
    assert "Mais populares" in page and "Novel Teste" in page


def test_home_shows_source_errors():
    client = TestClient(create_app(copy.deepcopy(DEFAULTS), FakeSource(fail=True), ManualQueue()))
    assert "site fora do ar" in client.get("/").text


def test_search(client):
    assert "Novel Teste" in client.get("/", params={"q": "teste"}).text
    assert "Nenhuma novel encontrada" in client.get("/", params={"q": "outra"}).text


def test_novel_page_groups_volumes_and_checks_default_outputs(client):
    page = client.get("/novel", params={"url": URL}).text
    assert "caps 1–2 · 2" in page and "Vol. Extra" in page
    assert 'value="audio" checked' in page and 'value="cbz" checked' in page and 'value="pdf" >' in page


def test_invalid_novel_link_is_rejected(client):
    assert client.get("/novel", params={"url": "https://outro.com/x"}).status_code == 400


def test_run_selects_volumes_and_numbers(client, queue):
    response = client.post(
        "/novel/run",
        data={"url": URL, "volumes": ["2"], "numbers": "1", "outputs": ["cbz"]},
        follow_redirects=False,
    )
    assert response.status_code == 303 and response.headers["location"] == "/jobs/1"
    job = queue.submitted[0]
    assert [c.number for c in job.chapters] == [1, 3] and job.outputs == {"cbz"}


def test_run_without_selection_is_rejected(client, queue):
    assert client.post("/novel/run", data={"url": URL, "outputs": ["cbz"]}).status_code == 400
    assert client.post("/novel/run", data={"url": URL, "numbers": "1-x", "outputs": ["cbz"]}).status_code == 400
    assert queue.submitted == []


def test_job_status_polls_only_while_active(client, queue):
    job = queue.submit("Novel Teste", CHAPTERS[:2], {"cbz"}, None)
    assert 'hx-trigger="every 2s"' in client.get(f"/jobs/{job.id}/status").text
    job.status, job.done = "done", 2
    status = client.get(f"/jobs/{job.id}/status").text
    assert "hx-trigger" not in status and "concluído" in status


def test_retry_runs_only_failed_chapters(client, queue):
    job = queue.submit("Novel Teste", CHAPTERS, {"cbz"}, "task")
    job.status, job.failures = "failed", [{"chapter": CHAPTERS[1], "reason": "sem token"}]
    response = client.post(f"/jobs/{job.id}/retry", follow_redirects=False)
    assert response.headers["location"] == "/jobs/2"
    assert queue.jobs[2].chapters == [CHAPTERS[1]] and queue.jobs[2].task == "task"


def test_redo_warned_reruns_only_chapters_with_alerts(client, queue):
    job = queue.submit("Novel Teste", CHAPTERS, {"audio"}, "task")
    job.status = "done"
    job.tracker.chapters = {
        (str(c.volume).strip(), c.number): {"key": (str(c.volume).strip(), c.number), "state": s}
        for c, s in zip(CHAPTERS[:3], ["ok", "warn", "ok"], strict=True)
    }
    response = client.post(f"/jobs/{job.id}/redo-warned", follow_redirects=False)
    assert response.headers["location"] == "/jobs/2"
    assert queue.jobs[2].chapters == [CHAPTERS[1]] and queue.jobs[2].redo


def test_unknown_job_is_404(client):
    assert client.get("/jobs/99").status_code == 404


def test_settings_never_show_the_token(client, monkeypatch):
    config = copy.deepcopy(DEFAULTS)
    config["abs"]["token"] = "segredo-123"
    monkeypatch.setattr(web_app.abs_api, "list_libraries", lambda abs_cfg: [])
    page = TestClient(create_app(config, FakeSource(), ManualQueue())).get("/settings").text
    assert "segredo-123" not in page and "definido" in page


def test_queue_runs_task_and_keeps_log_lines():
    queue = ManualQueue()

    def task(job):
        log("Cap 1 OK", "pronto")
        job.progress(1, 1)
        return {"failures": []}

    job = queue.submit("N", [CHAPTERS[0]], {"cbz"}, task)
    queue.run(job)
    assert job.status == "done" and job.percent == 100
    assert job.lines == ["[Cap 1 OK] pronto"]


def test_queue_marks_crashed_task_as_failed():
    queue = ManualQueue()

    def task(job):
        raise RuntimeError("boom")

    job = queue.submit("N", [CHAPTERS[0]], {"cbz"}, task)
    queue.run(job)
    assert job.status == "failed" and job.error == "boom"
    log("depois", "nao deve entrar")
    assert job.lines == []


def test_pause_stops_running_job_and_resume_requeues_the_rest(client, queue):
    calls = []
    job = queue.submit("N", CHAPTERS[:3], {"audio"}, None)
    job.status = "running"
    job.on_stop.append(lambda: calls.append("abort"))
    client.post(f"/jobs/{job.id}/pause")
    assert job.stop.is_set() and calls == ["abort"] and job.status == "stopping"

    def task(running):
        running.progress(1, 3)
        return {"failures": [], "stopped": CHAPTERS[1:3]}

    job.task = task
    job.status = "queued"
    queue.run(job)
    assert job.status == "paused" and job.remaining == CHAPTERS[1:3]
    client.post(f"/jobs/{job.id}/resume")
    assert job.status == "queued" and job.chapters == CHAPTERS[1:3] and queue._pending == [job]
    assert not job.stop.is_set()
    job.task = lambda running: (running.progress(2, 2), {"failures": [], "stopped": []})[1]
    queue.run(job)
    assert job.status == "done" and (job.done, job.total) == (3, 3)


def test_pausing_a_queued_job_keeps_all_chapters(client, queue):
    job = queue.submit("N", CHAPTERS[:2], {"cbz"}, None)
    client.post(f"/jobs/{job.id}/pause")
    assert job.status == "paused" and job.remaining == CHAPTERS[:2]


def test_search_with_a_novel_link_opens_the_novel(client):
    response = client.get("/", params={"q": URL}, follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"].startswith("/novel?url=https%3A")


def test_log_stops_polling_when_job_ends(client, queue):
    job = queue.submit("N", CHAPTERS[:1], {"cbz"}, None)
    job.lines = ["[Cap 1 OK] pronto"]
    assert client.get(f"/jobs/{job.id}/log").status_code == 200
    job.status = "done"
    response = client.get(f"/jobs/{job.id}/log")
    assert response.status_code == 286 and "[Cap 1 OK] pronto" in response.text


def test_static_css_is_served(client):
    assert client.get("/static/app.css").status_code == 200


def test_job_page_shows_phrase_and_map(client, queue):
    job = queue.submit("Novel Teste", CHAPTERS[:2], {"audio"}, None)
    job.tracker(
        "plan", {"chapters": CHAPTERS[:2], "outputs": {"audio"}, "download": [], "audio": CHAPTERS[:2], "skipped": []}
    )
    job.tracker("audio_start", {"chapter": CHAPTERS[0]})
    job.tracker("segments", {"ids": ["0000", "0001"], "done": []})
    job.tracker("segment_start", {"id": "0000", "text": "Uma frase sendo lida.", "worker": 0})
    page = client.get(f"/jobs/{job.id}").text
    assert "Uma frase sendo lida." in page and "Gerando agora" in page
    assert 'data-view="map"' in page and "Vol. 1" in page


@pytest.mark.parametrize(
    ("picked", "location"), [(r"C:\Livros\a b.pdf", "/epub?path=C%3A%5CLivros%5Ca%20b.pdf"), (None, "/epub")]
)
def test_epub_pick_redirects_with_the_chosen_path(client, monkeypatch, picked, location):
    monkeypatch.setattr(web_app, "file_picker", lambda: picked)
    response = client.get("/epub/pick", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == location
