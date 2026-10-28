import re

MIN_SIZE = 60
MAX_SIZE = 180
XTTS_LIMIT = 203

_ABBREVIATIONS = "Sr Sra Srta Dr Dra Prof Profa Ex Exa Ilmo Gen Cap Cel Maj Sgt Tte Av etc vs p pág nº No".split()
_RE_ABBREVIATION = re.compile(r"\b(" + "|".join(re.escape(a) for a in _ABBREVIATIONS) + r")\.", re.IGNORECASE)
_MARK = "\u0001"
_RE_SENTENCE_END = re.compile(r"(?<=[.!?…])([\"”’')\]]*)\s+(?=[\"“‘'(\[—–-]?\s*[A-ZÁÀÂÃÉÊÍÓÔÕÚÇ0-9\[])")
_RE_COMMA = re.compile(r"(?<=[,;:])\s+")


def split_paragraphs(paragraphs, min_size=MIN_SIZE, max_size=MAX_SIZE, sentence_pause_ms=250, paragraph_pause_ms=700):
    segments = []
    for paragraph in paragraphs:
        text = " ".join(str(paragraph).split())
        if not text:
            continue
        pieces = _split_paragraph(text, min_size, max_size)
        for i, piece in enumerate(pieces):
            last = i == len(pieces) - 1
            segments.append({"text": piece, "pause_ms": paragraph_pause_ms if last else sentence_pause_ms})
    for index, segment in enumerate(segments):
        segment["id"] = f"{index:04d}"
    return segments


def _split_paragraph(text, min_size, max_size):
    protected = _RE_ABBREVIATION.sub(lambda m: m.group(1) + _MARK, text)
    sentences = [s.replace(_MARK, ".").strip() for s in _RE_SENTENCE_END.split(protected) if s is not None]
    sentences = _reattach_closings(sentences)

    bounded = []
    for sentence in sentences:
        bounded.extend(_break_long(sentence, max_size))

    joined = []
    current = ""
    for sentence in bounded:
        if current and len(current) < min_size and len(current) + 1 + len(sentence) <= max_size:
            current = f"{current} {sentence}"
            continue
        if current:
            joined.append(current)
        current = sentence
    if current:
        if joined and len(current) < min_size // 2 and len(joined[-1]) + 1 + len(current) <= max_size:
            joined[-1] = f"{joined[-1]} {current}"
        else:
            joined.append(current)
    return joined


def _reattach_closings(parts):
    result = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if result and re.fullmatch(r"[\"”’')\]]+", part):
            result[-1] += part
        else:
            result.append(part)
    return result


def _break_long(sentence, max_size):
    if len(sentence) <= max_size:
        return [sentence]
    parts = [p for p in _RE_COMMA.split(sentence) if p]
    chunks = []
    current = ""
    for part in parts:
        if current and len(current) + 1 + len(part) > max_size:
            chunks.append(current)
            current = part
        else:
            current = f"{current} {part}".strip()
    if current:
        chunks.append(current)

    final = []
    for chunk in chunks:
        while len(chunk) > XTTS_LIMIT:
            cut = chunk.rfind(" ", 0, max_size)
            if cut <= 0:
                cut = max_size
            final.append(chunk[:cut].strip())
            chunk = chunk[cut:].strip()
        if chunk:
            final.append(chunk)
    return final
