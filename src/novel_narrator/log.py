import threading

_lock = threading.Lock()
_listeners = []


def log(tag, message):
    with _lock:
        print(f"[{tag}] {message}", flush=True)
        for listener in _listeners:
            listener(tag, message)


def add_listener(listener):
    _listeners.append(listener)


def remove_listener(listener):
    if listener in _listeners:
        _listeners.remove(listener)


def chapter_tag(chapter, stage=""):
    return f"Cap {chapter.number} {stage}".strip()


def chapter_label(chapter):
    volume = str(chapter.volume).strip()
    return f"Vol. {volume} - {chapter.title}" if volume else chapter.title


def summarize_chapters(chapters):
    by_volume = {}
    for chapter in chapters:
        by_volume.setdefault(str(chapter.volume), set()).add(chapter.number)
    return " | ".join(
        f"Vol {volume}: {_ranges(sorted(numbers))}" if volume.strip() else f"Caps {_ranges(sorted(numbers))}"
        for volume, numbers in by_volume.items()
    )


def _ranges(numbers):
    ranges = []
    start = previous = numbers[0]
    for number in numbers[1:] + [None]:
        if number is not None and number == previous + 1:
            previous = number
            continue
        ranges.append(str(start) if start == previous else f"{start}-{previous}")
        start = previous = number
    return ", ".join(ranges)


def format_duration(seconds):
    seconds = max(0, int(round(seconds)))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h{minutes:02d}min"
    if minutes:
        return f"{minutes}min{secs:02d}s"
    return f"{secs}s"
