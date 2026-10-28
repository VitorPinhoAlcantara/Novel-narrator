import json
import os
import zlib


def write_json_atomic(path, data):
    partial = path + ".tmp"
    with open(partial, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False)
    os.replace(partial, path)


def load_json(path):
    try:
        with open(path, encoding="utf-8") as file:
            return json.load(file)
    except (OSError, ValueError):
        return {}


def segment_seed(*parts):
    return zlib.crc32(":".join(str(p) for p in parts).encode("utf-8")) % 1000000


def classify_segments(segments, state, folder, uses_asr):
    reused, to_verify, to_generate = {}, [], []
    for segment in segments:
        previous = state.get(segment["id"])
        wav = os.path.join(folder, f"{segment['id']}.wav")
        usable = (
            previous
            and previous.get("text") == segment["text"]
            and previous["result"].get("ok")
            and os.path.exists(wav)
        )
        if usable:
            reused[segment["id"]] = previous["result"]
            if uses_asr and not previous.get("verified"):
                to_verify.append(segment["id"])
        else:
            to_generate.append(segment)
    return reused, to_verify, to_generate
