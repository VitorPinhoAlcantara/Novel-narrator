import hashlib
import json
import os
from pathlib import Path


def emotion_references(emotion_dir):
    root = Path(emotion_dir) if emotion_dir else None
    if root is None or not root.is_dir():
        return {}
    references = {}
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        wavs = sorted(str(w) for w in folder.glob("*.wav"))
        if wavs:
            references[folder.name] = wavs
    return references


def cache_key(wav_paths, reference, model_file):
    signature = json.dumps(
        [[p, os.path.getsize(p), int(os.path.getmtime(p))] for p in wav_paths]
        + [reference["gpt_cond_len"], reference["gpt_cond_chunk_len"], reference["max_ref_length"]]
        + [os.path.getsize(model_file), int(os.path.getmtime(model_file))]
    )
    return hashlib.sha1(signature.encode("utf-8")).hexdigest()[:16]


def conditioning_latents(model, wav_paths, reference, model_file, cache_dir, device):
    import torch

    path = os.path.join(cache_dir, f"latents_{cache_key(wav_paths, reference, model_file)}.pt") if cache_dir else ""
    if path and os.path.exists(path):
        return torch.load(path, map_location=device)
    latents = model.get_conditioning_latents(
        audio_path=list(wav_paths),
        load_sr=24000,
        sound_norm_refs=True,
        gpt_cond_len=reference["gpt_cond_len"],
        gpt_cond_chunk_len=reference["gpt_cond_chunk_len"],
        max_ref_length=reference["max_ref_length"],
    )
    if path:
        os.makedirs(cache_dir, exist_ok=True)
        torch.save(latents, path)
    return latents
