from novel_narrator.text.normalize import adjust_ending, normalize


def test_enclisis_hyphen_kept_by_default():
    assert normalize("Ele tentou iluminá-la agora.") == "Ele tentou iluminá-la agora."
    assert normalize("Ela far-se-á ouvir.") == "Ela far-se-á ouvir."


def test_enclisis_joined_optional():
    assert normalize("Quis dá-lo e disse-lhe.", {"pronoun_hyphen": "join"}) == "Quis dálo e disselhe."
    assert normalize("Ela far-se-á ouvir.", {"pronoun_hyphen": "join"}) == "Ela farseá ouvir."


def test_enclisis_spaced_optional():
    assert normalize("iluminá-la", {"pronoun_hyphen": "space"}) == "iluminá la"


def test_compound_kept_by_default():
    assert normalize("O guarda-chuva e o arco-íris.") == "O guarda-chuva e o arco-íris."


def test_dash_kept_by_default():
    assert normalize("— Você não pode — disse Nephis.") == "— Você não pode — disse Nephis."


def test_dash_removed_optional():
    text = "— Você não pode — disse Nephis. — Não agora."
    assert normalize(text, {"dash": "remove"}) == "Você não pode, disse Nephis. Não agora."


def test_dash_quotes_optional():
    assert normalize("— Sim.", {"dash": "quotes"}) == "“Sim.”"


def test_brackets_kept_by_default():
    assert normalize("o encantamento [Intacto] entrou") == "o encantamento [Intacto] entrou"


def test_numbers_as_words():
    assert normalize("Eram 349 dias.") == "Eram trezentos e quarenta e nove dias."


def test_abbreviations_expanded():
    assert normalize("O Sr. Silva e a Dra. Costa.") == "O Senhor Silva e a Doutora Costa."


def test_ellipsis_before_lowercase_becomes_comma():
    assert normalize("Hesitou… talvez.") == "Hesitou, talvez."


def test_ellipsis_before_uppercase_becomes_period():
    assert normalize("Hesitou... Talvez.") == "Hesitou. Talvez."


def test_leading_ellipsis_removed():
    assert normalize("… Claro, tudo bem.") == "Claro, tudo bem."


def test_adjust_ending_modes():
    assert adjust_ending("Frase.", "period") == "Frase."
    assert adjust_ending("Frase.", "none") == "Frase"
    assert adjust_ending("Frase.", "comma") == "Frase,"
    assert adjust_ending("Frase.", "space") == "Frase ."
    assert adjust_ending("Frase.", "semicolon_newline") == "Frase ;\n"
    assert adjust_ending("Frase!", "none") == "Frase!"
    assert adjust_ending("Será?", "semicolon_newline") == "Será?"


def test_leading_interjection_removed():
    assert normalize("Ha! Ele riu alto.", {"interjections": "remove"}) == "Ele riu alto."
    assert normalize("Argh! Ele rosnou.", {"interjections": "remove"}) == "Ele rosnou."
    assert normalize("Ha, ha, ha, ele riu.", {"interjections": "remove"}) == "ele riu."


def test_lone_interjection_becomes_empty():
    assert normalize("Ha!", {"interjections": "remove"}) == ""


def test_interjection_after_colon():
    assert normalize("Ele disse: Ha! Não.", {"interjections": "remove"}) == "Ele disse: Não."


def test_interjection_kept_by_default():
    assert normalize("Ha! Ele riu.") == "Ha! Ele riu."
    assert normalize("Argh! Ele rosnou.") == "Argh! Ele rosnou."


def test_regular_words_not_removed():
    assert normalize("Há muito tempo, o argumento era claro.") == "Há muito tempo, o argumento era claro."


def test_slash_between_numbers_becomes_de():
    assert normalize("Progresso: 0/1000") == "Progresso: zero de mil"
    assert normalize("Inimigos Derrotados: [12/50]") == "Inimigos Derrotados: [doze de cinquenta]"
    assert normalize("Foram 3 / 10 tentativas.") == "Foram três de dez tentativas."


def test_slash_with_thousands_separator():
    assert normalize("1.213/6.000") == "mil, duzentos e treze de seis mil"


def test_slash_outside_numbers_unchanged():
    assert normalize("sim/não e ele/ela") == "sim/não e ele/ela"
    assert normalize("Rank 5/A") == "Rank cinco/A"


def test_thousands_alone():
    assert normalize("Eram 10.000 soldados.") == "Eram dez mil soldados."
