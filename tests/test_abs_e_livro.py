import json

from centralnovel import audiolivro, audiolivro_config
from centralnovel.abs_api import bibliotecas_na_mesma_pasta, pasta_no_servidor
from centralnovel.download_utils import resumir_capitulos

MAPA = {
    "/audiobooks": "/home/topiinho/Code/production/audiobookshelf/audiobooks",
    "/podcasts": "/home/topiinho/Code/production/audiobookshelf/podcasts",
}


def test_pasta_do_container_vira_pasta_do_servidor():
    assert pasta_no_servidor("/audiobooks", MAPA) == "/home/topiinho/Code/production/audiobookshelf/audiobooks"
    assert pasta_no_servidor("/audiobooks/Laura", MAPA) == "/home/topiinho/Code/production/audiobookshelf/audiobooks/Laura"
    assert pasta_no_servidor("/audiobooks2", MAPA) is None
    assert pasta_no_servidor("/outra", MAPA) is None


def test_prefixo_mais_longo_vence():
    mapa = {"/audiobooks": "/a", "/audiobooks/laura": "/b"}
    assert pasta_no_servidor("/audiobooks/laura/x", mapa) == "/b/x"


def test_detecta_bibliotecas_na_mesma_pasta():
    libs = [{"id": "1", "nome": "Topiinho", "pastas": ["/audiobooks"]},
            {"id": "2", "nome": "Laura", "pastas": ["/audiobooks"]},
            {"id": "3", "nome": "Podcasts", "pastas": ["/podcasts"]},
            {"id": "4", "nome": "Sub", "pastas": ["/audiobooks/sub"]}]
    assert bibliotecas_na_mesma_pasta(libs, libs[1], "/audiobooks") == ["Topiinho", "Sub"]
    assert bibliotecas_na_mesma_pasta(libs, libs[2], "/podcasts") == []


CONFIG = {"formato": "m4b", "titulo_volume": "{novel} Volume {volume}", "idioma": "por"}


def test_livro_fica_em_autor_livro_arquivo():
    cap = {"capitulo": "4", "titulo": "Por que a Lua?", "volume": "", "livro": True}
    partes = audiolivro.caminho_relativo(CONFIG, "Sarah Faith Gottesdiener", "O livro da Lua", cap)
    assert partes == ("Sarah Faith Gottesdiener", "O livro da Lua", "004 - Por que a Lua.m4b")


def test_novel_continua_com_volume():
    cap = {"capitulo": "147", "titulo": "Poder", "volume": "2"}
    partes = audiolivro.caminho_relativo(CONFIG, "Guiltythree", "Shadow Slave", cap)
    assert partes == ("Guiltythree", "Shadow Slave", "Vol. 2 - Shadow Slave Volume 2", "0147 - Capitulo 147 - Poder.m4b")


def test_metadados_de_livro_sem_serie():
    sessao = audiolivro.SessaoAudiolivro.__new__(audiolivro.SessaoAudiolivro)
    sessao.config, sessao.autor, sessao.novel_title = CONFIG, "Autora", "O livro da Lua"
    meta = sessao._metadados({"titulo": "A Lua nova", "livro": True}, "", "", 9)
    assert meta["album"] == "O livro da Lua" and meta["title"] == "A Lua nova" and meta["track"] == "9"
    assert "series" not in meta


def test_resumo_de_capitulos_sem_volume():
    caps = [{"volume": "", "capitulo": str(n)} for n in (1, 2, 3, 7)]
    assert resumir_capitulos(caps) == "Caps 1-3, 7"
    assert resumir_capitulos([{"volume": "2", "capitulo": "5"}]) == "Vol 2: 5"


def test_salvar_config_preserva_outras_chaves(tmp_path, monkeypatch):
    arquivo = tmp_path / "audiobook_config.json"
    arquivo.write_text(json.dumps({"abs_token": "segredo", "abs_url": "http://velho"}), encoding="utf-8")
    monkeypatch.setattr(audiolivro_config, "ARQUIVO_CONFIG", str(arquivo))
    audiolivro_config.salvar_config({"abs_url": "http://novo", "abs_biblioteca": "Laura"})
    dados = json.loads(arquivo.read_text(encoding="utf-8"))
    assert dados == {"abs_token": "segredo", "abs_url": "http://novo", "abs_biblioteca": "Laura"}
    assert sorted(p.name for p in tmp_path.iterdir()) == ["audiobook_config.json"]


def test_fila_de_envio_separada_por_destino():
    base = {"abs_ssh": "topiinho@server"}
    minha = audiolivro_config.pasta_espera(dict(base, abs_remote_dir="/srv/abs/audiobooks"))
    dela = audiolivro_config.pasta_espera(dict(base, abs_remote_dir="/srv/abs/audiobooks_laura"))
    assert minha != dela
    assert minha.startswith(audiolivro_config.PASTA_ESPERA) and dela.startswith(audiolivro_config.PASTA_ESPERA)
    assert "audiobooks_laura_" in dela
    assert audiolivro_config.pasta_espera(dict(base, abs_remote_dir="/srv/abs/audiobooks")) == minha


def test_sem_ssh_usa_a_fila_padrao():
    assert audiolivro_config.pasta_espera({"abs_ssh": "", "abs_remote_dir": "/x"}) == audiolivro_config.PASTA_ESPERA


def test_destino_do_livro_usa_a_fila_do_proprio_destino():
    config = dict(CONFIG, abs_ssh="topiinho@server", abs_remote_dir="/srv/abs/audiobooks_laura")
    cap = {"capitulo": "1", "titulo": "Um", "volume": "", "livro": True}
    destino = audiolivro.caminho_destino(config, "Autora", "Livro", cap)
    assert destino.startswith(audiolivro_config.pasta_espera(config))
