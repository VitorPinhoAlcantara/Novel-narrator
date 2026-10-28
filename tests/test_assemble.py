import os
import shutil

import numpy as np
import pytest
import soundfile as sf

from novel_narrator.tts.assemble import (
    SAMPLE_RATE,
    assemble_chapter,
    crop,
    cut_at,
    detect_final_burst,
    encode_m4b,
    file_duration,
)

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="ffmpeg ausente"
)


def tone(seconds, amplitude=0.3, freq=220):
    t = np.arange(int(seconds * SAMPLE_RATE)) / SAMPLE_RATE
    return (amplitude * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def silence(seconds):
    return np.zeros(int(seconds * SAMPLE_RATE), dtype=np.float32)


def test_detects_short_burst_after_long_silence():
    wav = np.concatenate([tone(1.0), silence(1.1), tone(0.1, 0.1), silence(0.2)])
    burst = detect_final_burst(wav)
    assert burst is not None
    assert abs(burst["start"] - 2.1) < 0.03 and abs(burst["end"] - 2.2) < 0.03
    assert abs(burst["cut"] - 1.25) < 0.03


def test_cut_keeps_speech_and_fades_out():
    wav = np.concatenate([tone(1.0), silence(1.1), tone(0.1, 0.1)])
    cut = cut_at(wav, detect_final_burst(wav)["cut"])
    assert abs(len(cut) / SAMPLE_RATE - 1.25) < 0.03
    assert np.array_equal(cut[: int(1.1 * SAMPLE_RATE)], wav[: int(1.1 * SAMPLE_RATE)])
    assert abs(float(cut[-1])) < 1e-3


def test_burst_close_to_speech_is_ignored():
    assert detect_final_burst(np.concatenate([tone(1.0), silence(0.3), tone(0.1, 0.1)])) is None


def test_long_sound_after_silence_is_not_a_burst():
    assert detect_final_burst(np.concatenate([tone(1.0), silence(1.1), tone(1.5)])) is None


def test_no_burst_or_empty_audio():
    assert detect_final_burst(np.concatenate([tone(1.0), silence(0.4)])) is None
    assert detect_final_burst(np.zeros(100, dtype=np.float32)) is None
    assert detect_final_burst(silence(2.0)) is None


def test_crop_includes_margin():
    wav = np.concatenate([tone(1.0), silence(1.1), tone(0.1, 0.1)])
    burst = detect_final_burst(wav)
    assert 0.2 < len(crop(wav, burst["start"], burst["end"])) / SAMPLE_RATE < 0.5


def test_chapter_joins_segments_with_pauses(tmp_path):
    for segment_id in ("0000", "0001"):
        sf.write(tmp_path / f"{segment_id}.wav", tone(1.0), SAMPLE_RATE)
    segments = [{"id": "0000", "pause_ms": 250}, {"id": "0001", "pause_ms": 700}]
    duration = assemble_chapter(segments, tmp_path, tmp_path / "chapter.wav", lead_in_ms=600, tail_ms=1200)
    assert abs(duration - (0.6 + 1.0 + 0.25 + 1.0 + 0.7 + 1.2)) < 0.1


@needs_ffmpeg
def test_m4b_is_written_atomically(tmp_path):
    wav = str(tmp_path / "input.wav")
    sf.write(wav, tone(1.0), SAMPLE_RATE)
    destination = str(tmp_path / "out" / "chapter.m4b")
    encode_m4b(wav, destination, {"title": "x"}, loudnorm=False)
    assert os.path.exists(destination)
    assert not os.path.exists(destination + ".part")
    assert 0.8 < file_duration(destination) < 1.3


@needs_ffmpeg
def test_failed_encoding_leaves_no_file(tmp_path):
    destination = str(tmp_path / "out" / "chapter.m4b")
    with pytest.raises(RuntimeError):
        encode_m4b(str(tmp_path / "missing.wav"), destination, {"title": "x"}, loudnorm=False)
    assert not os.path.exists(destination)
    assert not os.path.exists(destination + ".part")
