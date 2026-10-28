import threading

_lock = threading.Lock()


def log(tag, message):
    with _lock:
        print(f"[{tag}] {message}", flush=True)


def format_duration(seconds):
    seconds = max(0, int(round(seconds)))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h{minutes:02d}min"
    if minutes:
        return f"{minutes}min{secs:02d}s"
    return f"{secs}s"
