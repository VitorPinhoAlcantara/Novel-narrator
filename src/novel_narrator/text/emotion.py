import re

EMOTIONS = ("neutral", "joy", "anger", "doubt")

_RE_ANGER = re.compile(
    r"\b(merda|maldit\w*|bastard\w*|droga|cale|saia|morr\w*|idiota|desgraça\w*|inferno|nunca)\b", re.IGNORECASE
)


def classify(text):
    if "?" in text:
        return "doubt"
    if "!" in text:
        return "anger" if _RE_ANGER.search(text) else "joy"
    return "neutral"
