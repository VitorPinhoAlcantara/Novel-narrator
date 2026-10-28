import threading

from centralnovel import downloader
from centralnovel.log import etiqueta_cap, formatar_duracao, log


def test_etiqueta_do_capitulo():
    cap = {"capitulo": "147", "volume": "2", "titulo": "Poder"}
    assert etiqueta_cap(cap, "audio") == "Cap 147 audio"
    assert etiqueta_cap(cap) == "Cap 147"
    assert etiqueta_cap({"capitulo": "0007"}, "download") == "Cap 7 download"


def test_formatar_duracao():
    assert formatar_duracao(45) == "45s"
    assert formatar_duracao(521) == "8min41s"
    assert formatar_duracao(8000) == "2h13min"
    assert formatar_duracao(-3) == "0s"


def test_linha_de_log_tem_etiqueta(capsys):
    log("Envio", "OK: 7 arquivo(s)")
    assert capsys.readouterr().out == "[Envio] OK: 7 arquivo(s)\n"


def test_linhas_de_threads_diferentes_nao_se_misturam(capsys):
    def escrever(prefixo):
        for i in range(200):
            log(prefixo, "x" * 50)

    threads = [threading.Thread(target=escrever, args=(f"T{n}",)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    linhas = capsys.readouterr().out.splitlines()
    assert len(linhas) == 800
    assert all(l.startswith("[T") and l.endswith("x" * 50) for l in linhas)


class SessaoFalsa:
    def __init__(self):
        self.n = 0

    def pendente(self, cap, sobrescrever=False):
        return int(cap["capitulo"]) != 2

    def gerar_capitulo(self, cap):
        self.n += 1
        return True, None

    def finalizar(self):
        pass


def _caps(n):
    return [{"volume": "1", "capitulo": str(i), "titulo": f"Titulo {i}", "url": "u"} for i in range(1, n + 1)]


def test_conclusao_do_capitulo_mostra_contagem_e_previsao(capsys, monkeypatch):
    monkeypatch.setattr(downloader, "_baixar_saidas_capitulo", lambda *a, **k: (True, None))
    downloader.download_capitulos_novel(_caps(4), "Novel Teste", saidas={"cbz", "audio"}, sessao_audio=SessaoFalsa())
    saida = capsys.readouterr().out
    assert "Novel Teste | 4 capitulo(s) | saidas: CBZ + Audiolivro" in saida
    assert "[Cap 1 audio] comecando o audio (item 1 de 4 da lista)" in saida
    assert "[Cap 1 OK] 1/4 concluidos, restam 3 | Vol. 1 - Titulo 1 | faltam 2 audio(s), previsao ~" in saida
    assert "[Cap 2 audio] ja existe no servidor, pulando" in saida
    assert "[Cap 4 OK] 4/4 concluidos, restam 0" in saida


def test_falha_aparece_com_motivo(capsys, monkeypatch):
    monkeypatch.setattr(downloader, "_baixar_saidas_capitulo", lambda *a, **k: (False, "sem token"))
    downloader.download_capitulos_novel(_caps(1), "Novel Teste", saidas={"cbz", "audio"}, sessao_audio=SessaoFalsa())
    assert "[Cap 1 ERRO] 1/1 concluidos, restam 0 | Vol. 1 - Titulo 1 | motivo: sem token" in capsys.readouterr().out
