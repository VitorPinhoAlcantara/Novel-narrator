import argparse
import json
import sys
import time

_STDOUT = sys.stdout
sys.stdout = sys.stderr


def _emit(obj):
    _STDOUT.write(json.dumps(obj, ensure_ascii=False) + "\n")
    _STDOUT.flush()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--model", default="openai/whisper-small")
    p.add_argument("--device", default="cuda")
    p.add_argument("--language", default="portuguese")
    args = p.parse_args()

    import soundfile as sf
    import torch
    from transformers import pipeline

    start = time.time()
    pipe = pipeline(
        "automatic-speech-recognition",
        model=args.model,
        device=args.device,
        dtype=torch.float16 if args.device.startswith("cuda") else torch.float32,
    )
    _emit({"event": "ready", "load_s": round(time.time() - start, 1)})

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        job = json.loads(line)
        if job.get("cmd") == "quit":
            break
        try:
            wav, rate = sf.read(job["wav"], dtype="float32")
            if wav.ndim > 1:
                wav = wav.mean(axis=1)
            output = pipe(
                {"raw": wav, "sampling_rate": rate},
                generate_kwargs={"language": args.language, "task": "transcribe"},
            )
            _emit({"event": "transcription", "id": job["id"], "text": output["text"].strip()})
        except Exception as exc:
            _emit({"event": "transcription", "id": job.get("id"), "text": "", "error": str(exc)})


if __name__ == "__main__":
    main()
