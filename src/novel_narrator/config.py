import copy
import os
import tomllib
from pathlib import Path

import tomli_w

from novel_narrator.text.normalize import DEFAULT_OPTIONS

DEFAULTS = {
    "paths": {
        "data_dir": "data",
        "voices_dir": "voices",
        "model_dir": "models/xtts_narrator",
        "pdf_dir": "PDF",
        "cbz_dir": "CBZ",
        "tts_python": "",
    },
    "outputs": {"audiobook": True, "cbz": True, "pdf": False},
    "pdf": {"remove_site_link": True, "remove_translation_mark": True},
    "cbz": {"jpg_quality": 95, "dpi": 150},
    "download": {"delay_s": 3, "max_retries": 3, "parallel": 3, "lookahead_chapters": 6},
    "text": {
        "segment_ending": "semicolon_newline",
        "pauses_ms": {"sentence": 250, "paragraph": 700},
        "normalization": dict(DEFAULT_OPTIONS),
    },
    "tts": {
        "voice": "voz_shadow_slave_ref.wav",
        "language": "pt",
        "device": "cuda",
        "matmul": "highest",
        "workers": 3,
        "adaptive_workers": True,
        "gpu_util_low": 35,
        "gpu_util_moderate": 70,
        "vram_per_worker_mb": 3500,
        "vram_reserve_mb": 2000,
        "vram_critical_mb": 1000,
        "vram_headroom_mb": 2500,
        "qa_attempts": 3,
        "trim_final_burst": True,
        "xtts": {"temperature": 0.55, "repetition_penalty": 2.0, "top_k": 30, "top_p": 0.7, "speed": 1.1},
        "reference": {"gpt_cond_len": 30, "gpt_cond_chunk_len": 6, "max_ref_length": 30},
        "qa": {},
        "emotion": {"enabled": False},
        "asr": {
            "enabled": True,
            "model": "openai/whisper-small",
            "rounds": 2,
            "limits": {},
            "retry_params": {"2": {"temperature": 0.7, "top_p": 0.85}},
        },
    },
    "audiobook": {
        "format": "m4b",
        "bitrate": "96k",
        "language": "por",
        "batch_chapters": 10,
        "volume_title": "{novel} Volume {volume}",
        "default_author": "Central Novel",
    },
    "abs": {"url": "", "token": "", "library": "", "local_dir": "", "ssh": "", "remote_dir": ""},
}


def load_config(path="config.toml", env_path=".env"):
    config = copy.deepcopy(DEFAULTS)
    path = Path(path)
    if path.exists():
        try:
            with path.open("rb") as file:
                _merge(config, tomllib.load(file), "")
        except tomllib.TOMLDecodeError as exc:
            raise ValueError(f"{path}: {exc}") from exc
    env = {**_read_env(Path(env_path)), **os.environ}
    token = env.get("ABS_TOKEN", "").strip()
    if token:
        config["abs"]["token"] = token
    return config


def save_config(changes, path="config.toml"):
    path = Path(path)
    data = {}
    if path.exists():
        with path.open("rb") as file:
            data = tomllib.load(file)
    _merge(data, changes, "")
    partial = path.with_name(path.name + ".tmp")
    with partial.open("wb") as file:
        tomli_w.dump(data, file)
    os.replace(partial, path)


def save_env(key, value, path=".env"):
    path = Path(path)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    kept = [line for line in lines if line.split("=", 1)[0].strip() != key]
    path.write_text("\n".join([*kept, f"{key}={value}"]) + "\n", encoding="utf-8")


def _merge(base, new, prefix):
    for key, value in new.items():
        name = f"{prefix}{key}"
        if isinstance(base.get(key), dict) and base[key]:
            if not isinstance(value, dict):
                raise ValueError(f"config key '{name}' must be a table")
            _merge(base[key], value, f"{name}.")
        else:
            base[key] = value


def _read_env(path):
    if not path.exists():
        return {}
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values
