import os
import subprocess

import numpy as np
import soundfile as sf

SAMPLE_RATE = 24000
_EDGE_DB = -48.0
_EDGE_MARGIN_S = 0.04
_WINDOW_S = 0.01


def trim_edges(wav, sample_rate=SAMPLE_RATE):
    window = max(1, int(sample_rate * _WINDOW_S))
    frames = len(wav) // window
    if frames == 0:
        return wav
    rms = np.sqrt(np.mean(wav[: frames * window].reshape(frames, window) ** 2, axis=1))
    active = np.flatnonzero(20.0 * np.log10(np.maximum(rms, 1e-6)) > _EDGE_DB)
    if len(active) == 0:
        return wav
    margin = int(sample_rate * _EDGE_MARGIN_S)
    start = max(0, active[0] * window - margin)
    end = min(len(wav), (active[-1] + 1) * window + margin)
    return wav[start:end]


def detect_final_burst(wav, sample_rate=SAMPLE_RATE, min_gap_s=0.6, max_burst_s=0.4, min_previous_s=0.3, keep_s=0.25):
    islands = _islands(wav, sample_rate)
    if len(islands) < 2:
        return None
    start, end = islands[-1]
    previous_start, previous_end = islands[-2]
    if (
        start - previous_end >= min_gap_s
        and end - start <= max_burst_s
        and previous_end - previous_start >= min_previous_s
    ):
        return {"start": start, "end": end, "cut": previous_end + keep_s}
    return None


def cut_at(wav, cut_s, sample_rate=SAMPLE_RATE, fade_s=0.03):
    cut = int(cut_s * sample_rate)
    if cut >= len(wav):
        return wav
    out = wav[:cut].copy()
    fade = min(int(fade_s * sample_rate), len(out))
    if fade > 0:
        out[-fade:] *= np.linspace(1.0, 0.0, fade, dtype=np.float32)
    return out


def crop(wav, start_s, end_s, sample_rate=SAMPLE_RATE, margin_s=0.15):
    a = max(0, int((start_s - margin_s) * sample_rate))
    b = min(len(wav), int((end_s + margin_s) * sample_rate))
    return wav[a:b]


def assemble_chapter(segments, wav_dir, output_wav, lead_in_ms=600, tail_ms=1200):
    parts = [_silence(lead_in_ms)]
    for segment in segments:
        path = os.path.join(wav_dir, f"{segment['id']}.wav")
        wav, sample_rate = sf.read(path, dtype="float32")
        if wav.ndim > 1:
            wav = wav.mean(axis=1)
        if sample_rate != SAMPLE_RATE:
            raise RuntimeError(f"taxa inesperada em {path}: {sample_rate}")
        parts.append(trim_edges(wav))
        parts.append(_silence(segment["pause_ms"]))
    parts.append(_silence(tail_ms))
    audio = np.concatenate(parts)
    os.makedirs(os.path.dirname(os.path.abspath(output_wav)), exist_ok=True)
    sf.write(output_wav, audio, SAMPLE_RATE, subtype="PCM_16")
    return len(audio) / SAMPLE_RATE


def file_duration(path):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


def encode_m4b(wav, destination, metadata, bitrate="96k", loudnorm=True):
    os.makedirs(os.path.dirname(os.path.abspath(destination)), exist_ok=True)
    partial = destination + ".part"
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", wav, "-vn"]
    if loudnorm:
        cmd += ["-af", "loudnorm=I=-18:TP=-1.5:LRA=11"]
    cmd += ["-ac", "1", "-ar", "44100", "-c:a", "aac", "-b:a", bitrate]
    for key, value in metadata.items():
        cmd += ["-metadata", f"{key}={value}"]
    cmd += ["-movflags", "+faststart+use_metadata_tags", "-f", "ipod", partial]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg: {result.stderr.strip()[:300]}")
        if file_duration(partial) <= 0:
            raise RuntimeError("ffmpeg gerou um arquivo sem duracao")
        os.replace(partial, destination)
    finally:
        if os.path.exists(partial):
            os.remove(partial)
    return destination


def _silence(ms):
    return np.zeros(int(SAMPLE_RATE * ms / 1000), dtype=np.float32)


def _islands(wav, sample_rate=SAMPLE_RATE, threshold_db=-50.0, merge_s=0.04):
    n = max(1, int(sample_rate * _WINDOW_S))
    frames = len(wav) // n
    if frames == 0:
        return []
    rms = np.sqrt(np.mean(wav[: frames * n].reshape(frames, n) ** 2, axis=1))
    active = 20.0 * np.log10(np.maximum(rms, 1e-7)) > threshold_db
    islands = []
    i = 0
    while i < frames:
        if active[i]:
            j = i
            while j < frames and active[j]:
                j += 1
            if islands and (i - islands[-1][1]) * _WINDOW_S < merge_s:
                islands[-1][1] = j
            else:
                islands.append([i, j])
            i = j
        else:
            i += 1
    return [(a * _WINDOW_S, b * _WINDOW_S) for a, b in islands]
