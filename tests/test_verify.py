from novel_narrator.tts.verify import analyze, has_similar_word

SENTENCE = "Agora ele finalmente era capaz de igualar Nephis e vencer a luta."


def test_same_transcript_passes():
    assert analyze("Ele ainda estava perdendo.", "ele ainda estava perdendo.")["ok"]


def test_different_proper_name_passes():
    assert analyze(
        "Antes que Nephis pudesse alcançá-lo, Sunny a evitou.", "Antes que nefis pudesse alcançá-lo, Sunny a evitou."
    )["ok"]


def test_spoken_period_at_end_fails():
    result = analyze("Ele ainda estava perdendo.", "ele ainda estava perdendo ponto")
    assert not result["ok"] and "spoken_period" in result["reasons"]


def test_skipped_words_fail():
    result = analyze(SENTENCE, "Agora ele finalmente")
    assert not result["ok"] and "skipped_words" in result["reasons"]


def test_digits_match_spelled_numbers():
    assert analyze("Capítulo trezentos e quarenta e nove: Destino", "Capítulo 349, Destino")["ok"]


def test_short_sentence_tolerates_one_word():
    assert analyze("Você não pode. Acabou, Neph.", "Você não pode. Acabou, Nefi.")["ok"]


def test_system_line_with_brackets_is_lenient():
    reference = "Inimigos Derrotados: [mil, duzentos e treze/seis mil]."
    assert analyze(reference, "Inimigos derrotados, 1.213 sobre 6.000")["ok"]


def test_system_line_still_flags_spoken_period():
    assert "spoken_period" in analyze("Memória: [M. Su, ndo].", "Memória M Su ndo ponto")["reasons"]


def test_similar_word_accepts_expected_ending():
    assert has_similar_word("Pedra.", "pedra")
    assert has_similar_word("Oh!", "pedra") is False
    assert has_similar_word("", "pedra") is False
