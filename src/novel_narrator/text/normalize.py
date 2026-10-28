import re

from num2words import num2words

DEFAULT_OPTIONS = {
    "pronoun_hyphen": "keep",
    "compound_hyphen": "keep",
    "dash": "keep",
    "ellipsis": "comma",
    "brackets": "keep",
    "numbers": "words",
    "inner_period": "normal",
    "abbreviations": "expand",
    "interjections": "keep",
}

_PRONOUNS = r"(?:se|me|te|lhe|lhes|lo|la|los|las|nos|vos|o|a|os|as|no|na|nas)"
_RE_ENCLISIS = re.compile(rf"(?<=[A-Za-zÀ-ÿ])-(?={_PRONOUNS}\b)", re.IGNORECASE)
_RE_MESOCLISIS = re.compile(rf"(?<=[A-Za-zÀ-ÿ])-({_PRONOUNS})-(?=[A-Za-zÀ-ÿ])", re.IGNORECASE)
_RE_LETTER_HYPHEN = re.compile(r"(?<=[A-Za-zÀ-ÿ])-(?=[A-Za-zÀ-ÿ])")
_RE_LEADING_DASH = re.compile(r"^\s*[—–]\s*")
_RE_INNER_DASH = re.compile(r"(?<=[.!?…])\s+[—–]\s+|\s+[—–]\s+")
_RE_ELLIPSIS = re.compile(r"\.{3,}|…")
_RE_BRACKETS = re.compile(r"\[([^\[\]]+)\]")
_RE_NUMBER = re.compile(r"(?<![\w.,])(\d{1,9})(?![\w.,]*\d)")
_RE_THOUSANDS = re.compile(r"(?<![\w.,])(\d{1,3}(?:\.\d{3})+)(?![\w.,]*\d)")
_RE_SLASH_NUMBERS = re.compile(r"(?<![\w/])(\d[\d.,]*)\s*/\s*(\d[\d.,]*)(?![\w/])")
_INTERJECTION = (
    r"(?:ha(?:ha)*h?|h[ae]h[ae]*|hee+|rá(?:[- ]?rá)*|a{1,3}rg+h*|ugh+|gr{2,}h*|h?m{2,}|hm+|hum+|uh+m*"
    r"|tsc|pf+t*|k{3,})"
)
_RE_INTERJECTION = re.compile(
    rf"(^|[.!?…:]\s+|[“\"—–-]\s*)(?:(?<![A-Za-zÀ-ÿ]){_INTERJECTION}(?![A-Za-zÀ-ÿ])[\s,!.?…-]*)+",
    re.IGNORECASE,
)
_RE_FINAL_PERIOD = re.compile(r"^(.*?[^.\s])\.([\"”’')\]]*)$", re.DOTALL)
_RE_ABBREVIATION = re.compile(r"\b(Sr|Sra|Srta|Dr|Dra|Prof|Profa|Cap)\.", re.IGNORECASE)
_EXPANSIONS = {
    "sr": "Senhor",
    "sra": "Senhora",
    "srta": "Senhorita",
    "dr": "Doutor",
    "dra": "Doutora",
    "prof": "Professor",
    "profa": "Professora",
    "cap": "Capítulo",
}


def normalize(text, options=None):
    cfg = {**DEFAULT_OPTIONS, **(options or {})}
    text = " ".join(str(text).split())

    if cfg["abbreviations"] == "expand":
        text = _RE_ABBREVIATION.sub(lambda m: _EXPANSIONS[m.group(1).lower()], text)
    if cfg["interjections"] == "remove":
        text = _RE_INTERJECTION.sub(r"\1", text)
    text = _apply_dash(text, cfg["dash"])
    text = _apply_brackets(text, cfg["brackets"])
    text = _apply_ellipsis(text, cfg["ellipsis"])
    text = _apply_hyphens(text, cfg["pronoun_hyphen"], cfg["compound_hyphen"])
    text = _RE_THOUSANDS.sub(lambda m: m.group(1).replace(".", ""), text)
    text = _RE_SLASH_NUMBERS.sub(r"\1 de \2", text)
    text = _apply_numbers(text, cfg["numbers"])
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = " ".join(text.split())
    if cfg["inner_period"] == "semicolon_newline":
        text = text.replace(".", " ;\n")
    return text


def adjust_ending(text, mode):
    text = text.rstrip()
    match = _RE_FINAL_PERIOD.match(text)
    if mode == "period" or not match:
        return text
    body, closing = match.group(1).rstrip(), match.group(2)
    endings = {
        "none": "",
        "comma": ",",
        "space": " .",
        "semicolon_newline": " ;\n",
        "period_newline": ".\n",
    }
    if mode not in endings:
        return text
    return body + endings[mode] + closing


def _apply_dash(text, mode):
    if mode == "keep":
        return text
    starts_dialogue = bool(_RE_LEADING_DASH.match(text))
    text = _RE_LEADING_DASH.sub("", text)
    text = _RE_INNER_DASH.sub(_replace_inner_dash, text)
    if mode == "quotes" and starts_dialogue:
        return f"“{text}”"
    return text


def _replace_inner_dash(m):
    before = m.string[: m.start()].rstrip()
    return " " if before.endswith((".", "!", "?", "…")) else ", "


def _apply_brackets(text, mode):
    if mode == "keep":
        return text
    if mode == "quotes":
        return _RE_BRACKETS.sub(r"“\1”", text)
    return _RE_BRACKETS.sub(r"\1", text)


def _apply_ellipsis(text, mode):
    if mode == "three_dots":
        return _RE_ELLIPSIS.sub("...", text)
    if mode == "comma":
        text = _RE_ELLIPSIS.sub("...", text)
        text = re.sub(r"^\s*\.{3}\s*", "", text)
        text = re.sub(r"\s*\.{3}\s*(?=[a-zà-ÿ])", ", ", text)
        text = re.sub(r"\s*\.{3}\s*$", ".", text)
        return re.sub(r"\s*\.{3}\s*(?=\S)", ". ", text)
    return text


def _apply_hyphens(text, pronoun_mode, compound_mode):
    replacements = {"join": "", "space": " "}
    if pronoun_mode in replacements:
        template = r"\1" if pronoun_mode == "join" else r" \1 "
        text = _RE_MESOCLISIS.sub(template, text)
        text = _RE_ENCLISIS.sub(replacements[pronoun_mode], text)
        text = _RE_ENCLISIS.sub(replacements[pronoun_mode], text)
    if compound_mode in replacements:
        text = _RE_LETTER_HYPHEN.sub(replacements[compound_mode], text)
    return text


def _apply_numbers(text, mode):
    if mode != "words":
        return text

    def replace(m):
        try:
            return num2words(int(m.group(1)), lang="pt_BR")
        except Exception:
            return m.group(1)

    return _RE_NUMBER.sub(replace, text)
