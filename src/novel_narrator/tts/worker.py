import argparse
import json
import os
import sys
import time

_STDOUT = sys.stdout
sys.stdout = sys.stderr


def _emit(obj):
    _STDOUT.write(json.dumps(obj, ensure_ascii=False) + "\n")
    _STDOUT.flush()


def _arguments():
    p = argparse.ArgumentParser()
    p.add_argument("--model-dir", required=True)
    p.add_argument("--voice", action="append", required=True)
    p.add_argument("--emotion-dir", default="")
    p.add_argument("--language", default="pt")
    p.add_argument("--device", default="cuda")
    p.add_argument("--latents-cache", default="")
    p.add_argument("--reference", default='{"gpt_cond_len": 30, "gpt_cond_chunk_len": 6, "max_ref_length": 30}')
    p.add_argument("--params", default="{}")
    p.add_argument("--qa", default="{}")
    p.add_argument("--matmul", default="highest")
    return p.parse_args()


def load_model(model_dir, device):
    import torch
    from TTS.tts.configs.xtts_config import XttsConfig
    from TTS.tts.models.xtts import Xtts

    config = XttsConfig()
    config.load_json(os.path.join(model_dir, "config.json"))
    model = Xtts.init_from_config(config)
    model.load_checkpoint(
        config,
        checkpoint_path=os.path.join(model_dir, "model.pth"),
        vocab_path=os.path.join(model_dir, "vocab.json"),
        eval=True,
    )
    model.to(torch.device(device))
    return model


def synthesize(model, gpt_latent, speaker_embedding, text, language, params, seed):
    import numpy as np
    import torch

    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    with torch.inference_mode():
        output = model.inference(
            text=text,
            language=language,
            gpt_cond_latent=gpt_latent,
            speaker_embedding=speaker_embedding,
            enable_text_splitting=False,
            **params,
        )
    wav = output["wav"]
    if torch.is_tensor(wav):
        wav = wav.detach().cpu().numpy()
    return np.asarray(wav, dtype=np.float32).reshape(-1)


def _serve(model, latents, speaker_embedding, args, base_params, limits, job):
    import soundfile as sf

    from novel_narrator.tts import qa

    text = job["text"]
    params = {**base_params, **(job.get("params") or {})}
    job_limits = {**limits, **(job.get("qa") or {})}
    attempts = max(1, int(job.get("attempts", 3)))
    base_seed = int(job.get("seed", 0))
    gpt_latent = latents.get(job.get("emotion"), latents["neutral"])

    best = None
    start = time.time()
    used = 0
    for n in range(attempts):
        used = n + 1
        wav = synthesize(model, gpt_latent, speaker_embedding, text, args.language, params, base_seed + n * 7919)
        metrics = qa.measure(wav, 24000, text)
        approved, reasons = qa.evaluate(metrics, job_limits)
        points = qa.score(metrics, job_limits)
        if best is None or points < best[0]:
            best = (points, wav, metrics, reasons)
        if approved:
            break

    _, wav, metrics, reasons = best
    os.makedirs(os.path.dirname(os.path.abspath(job["output"])), exist_ok=True)
    sf.write(job["output"], wav, 24000, subtype="PCM_16")
    return {
        "event": "result",
        "id": job["id"],
        "ok": not reasons,
        "attempts": used,
        "metrics": metrics,
        "reasons": reasons,
        "time_s": round(time.time() - start, 2),
    }


def main():
    args = _arguments()
    import torch

    from novel_narrator.tts import compat, voices

    compat.apply()
    try:
        torch.set_float32_matmul_precision(args.matmul)
    except Exception:
        pass

    start = time.time()
    model = load_model(args.model_dir, args.device)
    reference = json.loads(args.reference)
    model_file = os.path.join(args.model_dir, "model.pth")

    def latents_for(wavs):
        return voices.conditioning_latents(model, wavs, reference, model_file, args.latents_cache, args.device)

    gpt_latent, speaker_embedding = latents_for(args.voice)
    latents = {"neutral": gpt_latent}
    for name, wavs in voices.emotion_references(args.emotion_dir).items():
        latents[name] = latents_for(wavs)[0]
    base_params = json.loads(args.params)
    limits = json.loads(args.qa)
    vram = int(torch.cuda.memory_allocated() / 1024 / 1024) if torch.cuda.is_available() else 0
    _emit({"event": "ready", "vram_mb": vram, "load_s": round(time.time() - start, 1), "emotions": sorted(latents)})

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        job = json.loads(line)
        if job.get("cmd") == "quit":
            break
        try:
            _emit(_serve(model, latents, speaker_embedding, args, base_params, limits, job))
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception as exc:
            _emit(
                {
                    "event": "result",
                    "id": job.get("id"),
                    "ok": False,
                    "attempts": 0,
                    "metrics": {},
                    "reasons": [f"error: {exc}"],
                    "time_s": 0,
                }
            )


if __name__ == "__main__":
    main()
