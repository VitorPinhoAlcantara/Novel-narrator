import os
import shutil

import numpy as np
import pytest
import soundfile as sf

from centralnovel import audiolivro
from centralnovel.servidor_abs import _arquivos_locais
from centralnovel.tts.montar import TAXA, codificar_m4b, duracao_arquivo

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="ffmpeg ausente"
)


def gravar_tom(caminho, segundos):
    t = np.arange(int(segundos * TAXA)) / TAXA
    sf.write(caminho, (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32), TAXA)


def test_m4b_e_gravado_de_forma_atomica(tmp_path):
    wav = str(tmp_path / "entrada.wav")
    gravar_tom(wav, 1.0)
    destino = str(tmp_path / "saida" / "cap.m4b")
    codificar_m4b(wav, destino, {"title": "x"}, loudnorm=False)
    assert os.path.exists(destino)
    assert not os.path.exists(destino + ".part")
    assert 0.8 < duracao_arquivo(destino) < 1.3


def test_falha_na_codificacao_nao_deixa_arquivo(tmp_path):
    destino = str(tmp_path / "saida" / "cap.m4b")
    with pytest.raises(RuntimeError):
        codificar_m4b(str(tmp_path / "nao_existe.wav"), destino, {"title": "x"}, loudnorm=False)
    assert not os.path.exists(destino)
    assert not os.path.exists(destino + ".part")


def test_envio_ignora_arquivos_temporarios(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "ok.m4b").write_bytes(b"1234")
    (tmp_path / "a" / "cortado.m4b.part").write_bytes(b"12")
    assert list(_arquivos_locais(str(tmp_path))) == ["a/ok.m4b"]


def test_json_atomico_nao_deixa_temporario(tmp_path):
    caminho = str(tmp_path / "estado.json")
    audiolivro._gravar_json_atomico(caminho, {"a": 1})
    audiolivro._gravar_json_atomico(caminho, {"a": 2})
    assert os.listdir(tmp_path) == ["estado.json"]
    assert open(caminho, encoding="utf-8").read() == '{"a": 2}'


def test_espera_descarta_incompletos_e_mantem_validos(tmp_path, monkeypatch):
    espera = tmp_path / "espera"
    pasta = espera / "Autor" / "Novel" / "Vol"
    pasta.mkdir(parents=True)
    wav = str(tmp_path / "longo.wav")
    gravar_tom(wav, 31.0)
    codificar_m4b(wav, str(pasta / "0001 - bom.m4b"), {"title": "x"}, loudnorm=False)
    (pasta / "0002 - cortado.m4b").write_bytes(b"nao e um audio")
    (pasta / "0003 - temp.m4b.part").write_bytes(b"x")
    monkeypatch.setattr(audiolivro, "pasta_espera", lambda config: str(espera))
    monkeypatch.setattr(audiolivro, "PASTA_TRABALHO", str(tmp_path / "trabalho"))
    sessao = audiolivro.SessaoAudiolivro.__new__(audiolivro.SessaoAudiolivro)
    sessao.config = {"formato": "m4b"}
    sessao._descartar_incompletos_da_espera()
    assert sorted(os.listdir(pasta)) == ["0001 - bom.m4b"]
    assert os.listdir(tmp_path / "trabalho" / "descartados") == ["0002 - cortado.m4b"]


def _sessao():
    sessao = audiolivro.SessaoAudiolivro.__new__(audiolivro.SessaoAudiolivro)
    sessao.novel_title = "Novel"
    return sessao


def test_retoma_sem_regenerar_trecho_ja_gerado_mas_nao_verificado(tmp_path):
    for i in ("0000", "0001", "0002"):
        (tmp_path / f"{i}.wav").write_bytes(b"x")
    trechos = [{"id": "0000", "texto": "a"}, {"id": "0001", "texto": "b"}, {"id": "0002", "texto": "c"},
               {"id": "0003", "texto": "d"}]
    estado = {
        "0000": {"texto": "a", "res": {"ok": True}, "verificado": True},
        "0001": {"texto": "b", "res": {"ok": True}, "verificado": False},
        "0002": {"texto": "MUDOU", "res": {"ok": True}, "verificado": True},
    }
    reutilizados, para_verificar, a_gerar, _ = _sessao()._classificar(trechos, estado, str(tmp_path), "1", 1, True)
    assert sorted(reutilizados) == ["0000", "0001"]
    assert para_verificar == ["0001"]
    assert [t["id"] for t in a_gerar] == ["0002", "0003"]


def test_sem_whisper_trecho_gerado_conta_como_pronto(tmp_path):
    (tmp_path / "0000.wav").write_bytes(b"x")
    trechos = [{"id": "0000", "texto": "a"}]
    estado = {"0000": {"texto": "a", "res": {"ok": True}, "verificado": False}}
    reutilizados, para_verificar, a_gerar, _ = _sessao()._classificar(trechos, estado, str(tmp_path), "1", 1, False)
    assert list(reutilizados) == ["0000"] and para_verificar == [] and a_gerar == []
