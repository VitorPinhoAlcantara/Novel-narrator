from novel_narrator.text.split import XTTS_LIMIT, split_paragraphs


def test_sequential_ids_and_paragraph_pause():
    segments = split_paragraphs(
        [
            "Primeira frase com tamanho suficiente para ficar sozinha no trecho. "
            "Segunda frase tambem longa o bastante para isso.",
            "Outro paragrafo curto.",
        ],
        sentence_pause_ms=250,
        paragraph_pause_ms=700,
    )
    assert [s["id"] for s in segments] == [f"{i:04d}" for i in range(len(segments))]
    assert segments[0]["pause_ms"] == 250
    assert segments[-1]["pause_ms"] == 700


def test_abbreviation_does_not_split_sentence():
    segments = split_paragraphs(["O Sr. Silva chegou cedo e foi direto para a sala de reunioes no fim do corredor."])
    assert len(segments) == 1


def test_no_segment_exceeds_xtts_limit():
    long_text = ("palavra " * 80).strip() + "."
    for segment in split_paragraphs([long_text]):
        assert len(segment["text"]) <= XTTS_LIMIT


def test_short_fragment_joins_neighbor():
    assert len(split_paragraphs(["Agora ele finalmente era capaz de igualar Nephis. Quase."])) == 1


def test_empty_paragraphs_ignored():
    assert split_paragraphs(["", "   "]) == []
