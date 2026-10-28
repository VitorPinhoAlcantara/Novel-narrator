from novel_narrator.tts.voices import cache_key, emotion_references

REFERENCE = {"gpt_cond_len": 30, "gpt_cond_chunk_len": 6, "max_ref_length": 30}


def test_emotion_references_lists_folders_with_wavs(tmp_path):
    (tmp_path / "joy").mkdir()
    (tmp_path / "joy" / "b.wav").write_bytes(b"x")
    (tmp_path / "joy" / "a.wav").write_bytes(b"x")
    (tmp_path / "empty").mkdir()
    (tmp_path / "notes.txt").write_text("x")
    references = emotion_references(tmp_path)
    assert list(references) == ["joy"]
    assert [p.endswith(n) for p, n in zip(references["joy"], ["a.wav", "b.wav"], strict=True)] == [True, True]


def test_missing_or_disabled_emotion_dir_is_empty(tmp_path):
    assert emotion_references(tmp_path / "missing") == {}
    assert emotion_references("") == {}


def test_cache_key_changes_with_reference_settings(tmp_path):
    wav = tmp_path / "voice.wav"
    model = tmp_path / "model.pth"
    wav.write_bytes(b"x")
    model.write_bytes(b"y")
    key = cache_key([str(wav)], REFERENCE, str(model))
    assert key == cache_key([str(wav)], REFERENCE, str(model))
    assert key != cache_key([str(wav)], {**REFERENCE, "gpt_cond_len": 20}, str(model))
