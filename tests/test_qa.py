from novel_narrator.tts.qa import evaluate


def m(chars, duration, silence=0.0, energy=-20.0):
    return {
        "duration_s": duration,
        "chars": chars,
        "chars_per_s": round(chars / duration, 2),
        "max_silence_s": silence,
        "energy_db": energy,
        "clipping": 0.0,
    }


def test_real_short_sentences_are_not_stretched():
    assert evaluate(m(6, 1.109))[0]
    assert evaluate(m(4, 0.832))[0]
    assert evaluate(m(10, 1.856, 0.94))[0]
    assert evaluate(m(20, 3.435, 0.88))[0]
    assert evaluate(m(22, 3.381, 0.96))[0]


def test_really_stretched_short_sentence_fails():
    ok, reasons = evaluate(m(6, 6.0))
    assert not ok and "stretched" in reasons


def test_too_slow_normal_sentence_fails():
    ok, reasons = evaluate(m(120, 18.0))
    assert not ok and "stretched" in reasons


def test_normal_sentence_passes():
    assert evaluate(m(120, 7.5))[0]


def test_long_silence_fails():
    ok, reasons = evaluate(m(80, 12.0, silence=2.0))
    assert not ok and "long_silence" in reasons


def test_zero_limit_does_not_crash():
    ok, _ = evaluate(m(6, 1.0), {"chars_per_s_min": 0})
    assert ok
