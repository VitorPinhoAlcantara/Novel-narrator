import difflib
import re
import unicodedata

from num2words import num2words

DEFAULT_LIMITS = {
    "min_coverage": 0.85,
    "spoken_period_words": ("ponto", "pontos", "pont"),
    "max_missing_end": 2,
    "min_coverage_system_line": 0.4,
}


def words(text):
    text = _numbers_as_words(text)
    text = unicodedata.normalize("NFKD", str(text).lower().replace("-", " "))
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ]+", " ", text).split()


def analyze(reference, transcript, limits=None):
    lim = {**DEFAULT_LIMITS, **(limits or {})}
    ref = words(reference)
    hyp = _align(ref, words(transcript))
    if not ref:
        return {"ok": True, "reasons": [], "coverage": 1.0}

    matcher = difflib.SequenceMatcher(a=ref, b=hyp, autojunk=False)
    blocks = [b for b in matcher.get_matching_blocks() if b.size]
    matched = sum(b.size for b in blocks)
    coverage = matched / len(ref)
    ref_end = max((b.a + b.size for b in blocks), default=0)
    hyp_end = max((b.b + b.size for b in blocks), default=0)
    leftover = hyp[hyp_end:]

    reasons = []
    system_line = "[" in str(reference)
    base_minimum = lim["min_coverage_system_line"] if system_line else lim["min_coverage"]
    minimum = min(base_minimum, (len(ref) - 1) / len(ref))
    if coverage < minimum:
        reasons.append("skipped_words")
    if not system_line and len(ref) - ref_end > lim["max_missing_end"]:
        reasons.append("missing_end")
    if any(w in lim["spoken_period_words"] for w in leftover):
        reasons.append("spoken_period")
    return {"ok": not reasons, "reasons": reasons, "coverage": round(coverage, 3)}


def has_similar_word(text, target, similarity=0.7):
    return any(difflib.SequenceMatcher(a=w, b=target).ratio() >= similarity for w in words(text))


def _numbers_as_words(text):
    def replace(m):
        try:
            return num2words(int(m.group(0)), lang="pt_BR")
        except Exception:
            return m.group(0)

    return re.sub(r"\d{1,9}", replace, text)


def _align(ref, hyp, similarity=0.75):
    mapped = []
    candidates = set(ref)
    for word in hyp:
        best, best_ratio = word, 0.0
        for candidate in candidates:
            ratio = difflib.SequenceMatcher(a=word, b=candidate).ratio()
            if ratio > best_ratio:
                best, best_ratio = candidate, ratio
        mapped.append(best if best_ratio >= similarity else word)
    return mapped
