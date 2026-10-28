import copy
import sys
from pathlib import Path

import pytest

from novel_narrator.config import DEFAULTS
from novel_narrator.tts.pool import TTSPool

FAKE_WORKER = [sys.executable, "-u", str(Path(__file__).parent / "fake_worker.py")]


@pytest.fixture
def pool(tmp_path, monkeypatch):
    config = copy.deepcopy(DEFAULTS)
    config["paths"]["data_dir"] = str(tmp_path)
    config["tts"]["adaptive_workers"] = False
    pool = TTSPool(config, ["voice.wav"], workers=2)
    monkeypatch.setattr(pool, "_command", lambda: FAKE_WORKER)
    yield pool
    pool.close()


def test_all_jobs_get_a_result(pool):
    pool.start()
    done = []
    jobs = [{"id": f"{i:04d}", "text": "ok", "emotion": "joy"} for i in range(6)]
    results = pool.generate(jobs, on_done=lambda r, n, total: done.append((n, total)))
    assert sorted(results) == [j["id"] for j in jobs]
    assert all(r["ok"] and r["emotion"] == "joy" for r in results.values())
    assert sorted(done) == [(n, 6) for n in range(1, 7)]


def test_job_that_kills_every_worker_fails_without_hanging(pool):
    pool.start()
    results = pool.generate([{"id": "0000", "text": "die"}])
    assert results["0000"]["reasons"] == ["worker_died"]
    assert pool.workers == []


def test_job_requeued_after_a_crash_runs_on_a_live_worker(pool, tmp_path):
    pool.start()
    crash_once = tmp_path / "crashed"
    jobs = [{"id": "0000", "text": "ok"}, {"id": "0001", "text": "die_once", "flag": str(crash_once)}]
    results = pool.generate(jobs)
    assert results["0001"]["ok"] is True
    assert len(pool.workers) == 1


def test_command_points_to_emotion_dir_only_when_enabled(tmp_path):
    config = copy.deepcopy(DEFAULTS)
    command = TTSPool(config, ["v.wav"])._command()
    assert "--emotion-dir" in command
    config["tts"]["emotion"]["enabled"] = False
    assert "--emotion-dir" not in TTSPool(config, ["v.wav"])._command()
