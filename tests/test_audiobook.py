import copy
import os
import shutil

import numpy as np
import pytest
import soundfile as sf

from novel_narrator.config import DEFAULTS
from novel_narrator.models import Chapter, Novel
from novel_narrator.pipeline.audiobook import (
    AudiobookSession,
    chapter_metadata,
    relative_path,
    safe_name,
    volume_epub_path,
)
from novel_narrator.pipeline.state import classify_segments, write_json_atomic
from novel_narrator.publish.abs_server import outbox_dir, publish_root
from novel_narrator.sources.base import Source
from novel_narrator.tts.assemble import SAMPLE_RATE, encode_m4b

CONFIG = copy.deepcopy(DEFAULTS)


def session(tmp_path, book_mode=False, **abs_values):
    config = copy.deepcopy(DEFAULTS)
    config["paths"]["data_dir"] = str(tmp_path / "data")
    config["abs"].update(abs_values)
    return AudiobookSession(Source(), Novel("Shadow Slave", author="Guiltythree"), config, book_mode=book_mode)


def test_book_goes_to_author_book_file():
    chapter = Chapter(4, "Por que a Lua?", volume="")
    parts = relative_path(CONFIG, "Sarah Faith Gottesdiener", "O livro da Lua", chapter, book_mode=True)
    assert parts == ("Sarah Faith Gottesdiener", "O livro da Lua", "004 - Por que a Lua.m4b")


def test_novel_keeps_volume_folder():
    parts = relative_path(CONFIG, "Guiltythree", "Shadow Slave", Chapter(147, "Poder", volume="2"))
    assert parts == (
        "Guiltythree",
        "Shadow Slave",
        "Vol. 2 - Shadow Slave Volume 2",
        "0147 - Capitulo 147 - Poder.m4b",
    )


def test_volume_epub_sits_next_to_the_audio():
    assert volume_epub_path(CONFIG, "G", "Shadow Slave", "2") == (
        "G",
        "Shadow Slave",
        "Vol. 2 - Shadow Slave Volume 2",
        "Shadow Slave Volume 2.epub",
    )


def test_book_metadata_has_no_series():
    meta = chapter_metadata(CONFIG, "Autora", "O livro da Lua", Chapter(9, "A Lua nova", volume=""), "", True)
    assert (meta["album"], meta["title"], meta["track"]) == ("O livro da Lua", "A Lua nova", "9")
    assert "series" not in meta


def test_novel_metadata_has_series_and_part():
    meta = chapter_metadata(CONFIG, "G", "Shadow Slave", Chapter(147, "Poder", volume="2"), "Capítulo 147: Poder")
    assert meta["series"] == "Shadow Slave" and meta["series-part"] == "2" and meta["title"] == "Capítulo 147: Poder"


def test_safe_name_strips_forbidden_characters():
    assert safe_name('Quem? Eu: "nao" / sim... ') == "Quem Eu nao sim"


def test_atomic_json_leaves_no_temp_file(tmp_path):
    path = str(tmp_path / "state.json")
    write_json_atomic(path, {"a": 1})
    write_json_atomic(path, {"a": 2})
    assert os.listdir(tmp_path) == ["state.json"]
    assert open(path, encoding="utf-8").read() == '{"a": 2}'


def test_resume_reuses_generated_segments_and_redoes_changed_ones(tmp_path):
    for i in ("0000", "0001", "0002"):
        (tmp_path / f"{i}.wav").write_bytes(b"x")
    segments = [{"id": f"000{i}", "text": t} for i, t in enumerate("abcd")]
    state = {
        "0000": {"text": "a", "result": {"ok": True}, "verified": True},
        "0001": {"text": "b", "result": {"ok": True}, "verified": False},
        "0002": {"text": "CHANGED", "result": {"ok": True}, "verified": True},
    }
    reused, to_verify, to_generate = classify_segments(segments, state, str(tmp_path), True)
    assert sorted(reused) == ["0000", "0001"]
    assert to_verify == ["0001"]
    assert [s["id"] for s in to_generate] == ["0002", "0003"]


def test_without_whisper_generated_segment_counts_as_done(tmp_path):
    (tmp_path / "0000.wav").write_bytes(b"x")
    state = {"0000": {"text": "a", "result": {"ok": True}, "verified": False}}
    reused, to_verify, to_generate = classify_segments([{"id": "0000", "text": "a"}], state, str(tmp_path), False)
    assert list(reused) == ["0000"] and to_verify == [] and to_generate == []


def test_job_carries_emotion_and_retry_params(tmp_path):
    s = session(tmp_path)
    first = s._job({"id": "0001", "text": "Ele veio."}, str(tmp_path), 10, 0)
    question = s._job({"id": "0002", "text": "Será que ele vem?"}, str(tmp_path), 10, 0)
    retry = s._job({"id": "0001", "text": "Conseguimos!"}, str(tmp_path), 10, 2)
    assert first["emotion"] == "neutral" and first["text"] == "Ele veio ;\n" and "params" not in first
    assert question["emotion"] == "doubt" and question["text"] == "Será que ele vem?"
    assert retry["emotion"] == "joy" and retry["seed"] == 10 + 2 * 104729
    assert retry["params"] == {"temperature": 0.7, "top_p": 0.85}


def test_destination_uses_the_outbox_of_its_own_server(tmp_path):
    s = session(tmp_path, ssh="me@server", remote_dir="/srv/abs/audiobooks_laura")
    assert os.path.basename(publish_root(s.config)).startswith("audiobooks_laura_")


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg ausente")
def test_outbox_discards_incomplete_audio_and_keeps_valid(tmp_path):
    s = session(tmp_path, ssh="me@server", remote_dir="/srv/abs/audiobooks")
    folder = os.path.join(outbox_dir(s.config), "Autor", "Novel", "Vol")
    os.makedirs(folder)
    t = np.arange(int(31 * SAMPLE_RATE)) / SAMPLE_RATE
    wav = str(tmp_path / "long.wav")
    sf.write(wav, (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32), SAMPLE_RATE)
    encode_m4b(wav, os.path.join(folder, "0001 - bom.m4b"), {"title": "x"}, loudnorm=False)
    with open(os.path.join(folder, "0002 - cortado.m4b"), "wb") as file:
        file.write(b"nao e um audio")
    with open(os.path.join(folder, "0003 - temp.m4b.part"), "wb") as file:
        file.write(b"x")
    s._discard_incomplete_outbox()
    assert sorted(os.listdir(folder)) == ["0001 - bom.m4b"]
    assert os.listdir(os.path.join(s.data_dir, "discarded")) == ["0002 - cortado.m4b"]
