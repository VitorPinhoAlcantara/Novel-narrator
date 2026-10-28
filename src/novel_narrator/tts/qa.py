import numpy as np

DEFAULT_LIMITS = {
    "chars_per_s_min": 8.0,
    "chars_per_s_max": 28.0,
    "max_silence_s": 1.2,
    "max_duration_s": 27.0,
    "min_energy_db": -45.0,
    "max_clipping": 0.002,
    "short_chars_max": 40,
    "short_base_s": 1.5,
}

_WINDOW_S = 0.02
_SILENCE_DB = -50.0


def measure(wav, sample_rate, text):
    wav = np.asarray(wav, dtype=np.float32).reshape(-1)
    duration = len(wav) / sample_rate if sample_rate else 0.0
    chars = len(text.strip())
    metrics = {
        "duration_s": round(duration, 3),
        "chars": chars,
        "chars_per_s": round(chars / duration, 2) if duration > 0 else 0.0,
        "max_silence_s": 0.0,
        "energy_db": -120.0,
        "clipping": 0.0,
    }
    if len(wav) == 0:
        return metrics

    metrics["clipping"] = round(float(np.mean(np.abs(wav) >= 0.999)), 5)
    metrics["energy_db"] = round(_db(float(np.sqrt(np.mean(wav**2)))), 1)

    window = max(1, int(sample_rate * _WINDOW_S))
    frames = len(wav) // window
    if frames == 0:
        return metrics
    rms = np.sqrt(np.mean(wav[: frames * window].reshape(frames, window) ** 2, axis=1))
    silent = _db_array(rms) < _SILENCE_DB
    metrics["max_silence_s"] = round(_longest_inner_silence(silent) * _WINDOW_S, 2)
    return metrics


def evaluate(metrics, limits=None):
    lim = {**DEFAULT_LIMITS, **(limits or {})}
    reasons = []
    if metrics["duration_s"] <= 0.05:
        return False, ["empty"]
    if metrics["energy_db"] < lim["min_energy_db"]:
        reasons.append("silent")
    if metrics["duration_s"] > lim["max_duration_s"]:
        reasons.append("did_not_stop")
    if _stretched(metrics, lim):
        reasons.append("stretched")
    if metrics["chars_per_s"] > lim["chars_per_s_max"]:
        reasons.append("truncated")
    if metrics["max_silence_s"] > lim["max_silence_s"]:
        reasons.append("long_silence")
    if metrics["clipping"] > lim["max_clipping"]:
        reasons.append("clipping")
    return not reasons, reasons


def score(metrics, limits=None):
    lim = {**DEFAULT_LIMITS, **(limits or {})}
    _, reasons = evaluate(metrics, lim)
    points = float(len(reasons))
    points += max(0.0, metrics["max_silence_s"] - lim["max_silence_s"]) / 5.0
    center = (lim["chars_per_s_min"] + lim["chars_per_s_max"]) / 2
    points += abs(metrics["chars_per_s"] - center) / 100.0
    return points


def _stretched(metrics, lim):
    if metrics["chars"] < lim["short_chars_max"]:
        return metrics["duration_s"] > lim["short_base_s"] + metrics["chars"] / max(lim["chars_per_s_min"], 1e-6)
    return metrics["chars_per_s"] < lim["chars_per_s_min"]


def _longest_inner_silence(silent):
    voiced = np.flatnonzero(~silent)
    if len(voiced) == 0:
        return 0
    inner = silent[voiced[0] : voiced[-1] + 1]
    longest = current = 0
    for frame in inner:
        current = current + 1 if frame else 0
        longest = max(longest, current)
    return longest


def _db(value):
    return 20.0 * float(np.log10(max(value, 1e-6)))


def _db_array(values):
    return 20.0 * np.log10(np.maximum(values, 1e-6))
