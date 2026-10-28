from novel_narrator.text.emotion import EMOTIONS, classify


def test_question_is_doubt_even_with_exclamation():
    assert classify("Será que ele vem?!") == "doubt"


def test_exclamation_with_anger_word_is_anger():
    assert classify("Saia daqui agora! Seu idiota!") == "anger"


def test_exclamation_without_anger_word_is_joy():
    assert classify("Conseguimos!") == "joy"


def test_plain_sentence_is_neutral():
    assert classify("O sol se pôs atrás das montanhas.") == "neutral"


def test_every_result_is_a_known_emotion():
    assert {classify(t) for t in ["a?", "a!", "merda!", "a."]} <= set(EMOTIONS)
