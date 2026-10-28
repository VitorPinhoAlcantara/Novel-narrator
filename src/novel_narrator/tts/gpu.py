import subprocess
import time

_QUERY = ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total", "--format=csv,noheader,nounits"]


def sample():
    output = subprocess.run(_QUERY, capture_output=True, text=True, timeout=10).stdout
    util, used, total = [int(x) for x in output.strip().splitlines()[0].split(",")]
    return {"util": util, "used_mb": used, "total_mb": total}


def average_util(seconds=2.0, interval=0.5):
    samples = []
    end = time.time() + seconds
    while True:
        samples.append(sample()["util"])
        if time.time() >= end:
            break
        time.sleep(interval)
    return sum(samples) / len(samples)


def decide_workers(external_util, used_mb, total_mb, current_workers, tts):
    maximum = max(1, int(tts["workers"]))
    if external_util <= tts["gpu_util_low"]:
        by_util = maximum
    elif external_util <= tts["gpu_util_moderate"]:
        by_util = min(2, maximum)
    else:
        by_util = 1

    per_worker = tts["vram_per_worker_mb"]
    external_mb = max(0, used_mb - current_workers * per_worker)
    by_memory = int((total_mb - external_mb - tts["vram_reserve_mb"]) // per_worker)
    return max(1, min(maximum, by_util, by_memory))


def choose_workers(tts, current_workers=0):
    if not tts.get("adaptive_workers", True):
        return max(1, int(tts["workers"])), None
    try:
        util = average_util()
        current = sample()
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return max(1, int(tts["workers"])), None
    n = decide_workers(util, current["used_mb"], current["total_mb"], current_workers, tts)
    return n, {"util": round(util), "used_mb": current["used_mb"], "total_mb": current["total_mb"]}
