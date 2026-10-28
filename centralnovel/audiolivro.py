import json
import os
import re
import shutil
import threading
import time
import zlib

import requests
import soundfile as sf

from .audiolivro_config import PASTA_ESPERA, PASTA_TRABALHO, carregar_config, pasta_espera
from .download_utils import resumir_capitulos
from .epub_writer import criar_epub_volume
from .log import etiqueta_cap, formatar_duracao, log, rotulo_capitulo
from .servidor_abs import (
    enviar_espera,
    listar_existentes,
    modo_remoto,
    raiz_publicacao,
    verificar_destino,
)
from .texto_capitulo import baixar_capa, obter_info_novel, obter_texto_capitulo
from .tts.dividir import dividir_paragrafos
from .tts.gpu import escolher_workers
from .tts.montar import (
    cortar_em,
    detectar_rajada_final,
    codificar_m4b,
    duracao_arquivo,
    montar_capitulo,
    recortar,
)
from .tts.normalizar import ajustar_final, normalizar
from .tts.pool import PoolTTS
from .tts.verificar import analisar, palavras, tem_palavra_parecida


def separar_pendentes(capitulos, novel_title, info, config):
    autor = info.get("autor") or config["autor_padrao"]
    existentes = _listar_existentes(config, autor, novel_title)
    prontos, pendentes = [], []
    for cap in capitulos:
        pronto = caminho_relativo(config, autor, novel_title, cap) in existentes
        (prontos if pronto else pendentes).append(cap)
    return prontos, pendentes


def caminho_relativo(config, autor, novel_title, cap):
    if cap.get("livro"):
        nome = f"{int(cap['capitulo']):03d} - {cap['titulo']}.{config['formato']}"
        return (_nome_seguro(autor), _nome_seguro(novel_title), _nome_seguro(nome))
    volume = str(cap["volume"]).strip()
    titulo_vol = config["titulo_volume"].format(novel=novel_title, volume=volume)
    pasta_livro = f"Vol. {volume} - {titulo_vol}" if volume.isdigit() else titulo_vol
    numero = int(cap["capitulo"])
    nome = f"{numero:04d} - Capitulo {numero} - {cap['titulo']}.{config['formato']}"
    return (
        _nome_seguro(autor), _nome_seguro(novel_title), _nome_seguro(pasta_livro), _nome_seguro(nome),
    )


def caminho_epub_volume(config, autor, novel_title, volume):
    cap_exemplo = {"volume": volume, "capitulo": "1", "titulo": "x"}
    base = caminho_relativo(config, autor, novel_title, cap_exemplo)[:-1]
    titulo_vol = config["titulo_volume"].format(novel=novel_title, volume=volume)
    return base + (_nome_seguro(f"{titulo_vol}.epub"),)


def caminho_destino(config, autor, novel_title, cap):
    raiz = raiz_publicacao(config, pasta_espera(config))
    return os.path.join(raiz, *caminho_relativo(config, autor, novel_title, cap))


def escanear_biblioteca(config):
    token = config.get("abs_token", "")
    if not token:
        log("Envio", "AVISO: sem token do Audiobookshelf (abs_token ou ABS_TOKEN); faca o scan da biblioteca manualmente")
        return False
    base = config["abs_url"].rstrip("/")
    headers = {"Authorization": f"Bearer {token}"}
    try:
        resposta = requests.get(f"{base}/api/libraries", headers=headers, timeout=15)
        resposta.raise_for_status()
        bibliotecas = resposta.json().get("libraries", [])
        nome = config.get("abs_biblioteca", "")
        escolhida = next((b for b in bibliotecas if b.get("name") == nome), None) if nome else None
        if escolhida is None and len(bibliotecas) == 1:
            escolhida = bibliotecas[0]
        if escolhida is None:
            log("Envio", "AVISO: defina 'abs_biblioteca' no audiobook_config.json "
                         f"(disponiveis: {', '.join(b.get('name', '?') for b in bibliotecas)})")
            return False
        scan = requests.post(f"{base}/api/libraries/{escolhida['id']}/scan",
                             headers=headers, timeout=30)
        scan.raise_for_status()
        log("Envio", f"scan do Audiobookshelf solicitado (biblioteca {escolhida.get('name')})")
        return True
    except requests.RequestException as exc:
        log("Envio", f"AVISO: nao foi possivel acionar o scan do Audiobookshelf: {exc}")
        return False


class SessaoAudiolivro:
    def __init__(self, novel_title, url_novel, voz, config=None, info=None):
        self.novel_title = novel_title
        self.url_novel = url_novel
        self.voz = voz
        self.config = config or carregar_config()
        self.info = info
        self.autor = None
        self.existentes = set()
        self.pool = None
        self.publicados = []
        self.volumes_com_capa = set()
        self._preparada = False
        self._erro_preparo = None
        self._etq = "audio"

    def _log(self, mensagem):
        log(getattr(self, "_etq", "audio"), mensagem)

    def pendente(self, cap, sobrescrever=False):
        self._preparar()
        if self._erro_preparo:
            return True
        if sobrescrever:
            return True
        return caminho_relativo(self.config, self.autor, self.novel_title, cap) not in self.existentes

    def gerar_capitulo(self, cap):
        self._preparar()
        if self._erro_preparo:
            return False, self._erro_preparo
        if "paragrafos" in cap:
            paragrafos = list(cap["paragrafos"])
        else:
            try:
                paragrafos = obter_texto_capitulo(cap)
            except RuntimeError as exc:
                return False, f"texto indisponivel: {exc}"

        numero = int(cap["capitulo"])
        volume = str(cap["volume"]).strip()
        self._etq = etiqueta_cap(cap, "audio")
        inicio_cap = time.time()
        titulo_cap = cap.get("titulo_falado", f"Capítulo {numero}: {cap['titulo']}")
        self._garantir_capa(cap)
        if not cap.get("livro"):
            self._guardar_texto(volume, numero, titulo_cap, paragrafos)

        opcoes = self.config.get("normalizacao", {})
        textos = [normalizar(p, opcoes) for p in ([titulo_cap] if titulo_cap else []) + list(paragrafos)]
        pausas = self.config["pausas_ms"]
        trechos = dividir_paragrafos(
            [t for t in textos if t],
            pausa_frase_ms=pausas["frase"],
            pausa_paragrafo_ms=pausas["paragrafo"],
        )
        if not trechos:
            return False, "capitulo sem texto apos a normalizacao"

        pasta = os.path.join(PASTA_TRABALHO, _nome_seguro(self.novel_title), f"cap_{volume}_{numero:04d}")
        self._log(f"{rotulo_capitulo(cap)} | texto dividido em {len(trechos)} trechos")
        try:
            resultados = self._sintetizar(trechos, pasta, volume, numero)
        except KeyboardInterrupt:
            salvos = len([a for a in os.listdir(pasta) if a.endswith(".wav")]) if os.path.isdir(pasta) else 0
            self._log(f"INTERROMPIDO: {salvos} de {len(trechos)} trechos guardados em {pasta}. "
                      "Este capitulo NAO sera enviado ao servidor e continua de onde parou na proxima vez")
            raise
        except RuntimeError as exc:
            return False, str(exc)

        ausentes = [t["id"] for t in trechos if not os.path.exists(os.path.join(pasta, f"{t['id']}.wav"))]
        if ausentes:
            return False, f"{len(ausentes)} trecho(s) sem audio (ex.: {', '.join(ausentes[:5])})"

        caminho_qa = self._gravar_relatorio_qa(volume, numero, trechos, resultados)
        self._log("montando o audio e codificando o m4b...")
        wav = os.path.join(pasta, "capitulo.wav")
        destino = caminho_destino(self.config, self.autor, self.novel_title, cap)
        try:
            duracao = montar_capitulo(trechos, pasta, wav)
            codificar_m4b(wav, destino, self._metadados(cap, titulo_cap, volume, numero),
                          bitrate=self.config["bitrate"])
        except (OSError, RuntimeError) as exc:
            return False, f"falha ao montar o audio: {exc}"

        com_whisper = sum(1 for r in resultados.values() if r.get("asr_motivos"))
        so_numerico = sum(1 for r in resultados.values() if not r.get("ok") and not r.get("asr_motivos"))
        relatorio = f" | relatorio: {caminho_qa}" if (com_whisper or so_numerico) else ""
        self._log(f"pronto em {formatar_duracao(time.time() - inicio_cap)}: {duracao / 60:.1f} min de audio, "
                  f"{len(trechos)} trechos | alertas: Whisper {com_whisper}, QA numerico {so_numerico}{relatorio}")
        shutil.rmtree(pasta, ignore_errors=True)
        self.publicados.append(cap)
        self.existentes.add(caminho_relativo(self.config, self.autor, self.novel_title, cap))
        if len(self.publicados) >= max(1, int(self.config["lote_capitulos"])):
            self._publicar_lote()
        return True, None

    def finalizar(self):
        try:
            self._publicar_lote()
        finally:
            if self.pool is not None:
                self.pool.encerrar()
                self.pool = None

    def _preparar(self):
        if self._preparada:
            return
        self._preparada = True
        erro = self._validar_ambiente()
        if erro:
            self._erro_preparo = erro
            log("Preparo", f"ERRO: {erro}")
            return
        try:
            if self.info is None:
                self.info = obter_info_novel(self.url_novel)
            self.autor = self.info.get("autor") or self.config["autor_padrao"]
            log("Preparo", f"autor: {self.autor}")
            self._descartar_incompletos_da_espera()
            self._avisar_fila_sem_destino()
            reenviados = enviar_espera(self.config, pasta_espera(self.config))
            if reenviados:
                log("Preparo", f"{reenviados} arquivo(s) de execucoes anteriores que aguardavam envio foram enviados ao servidor")
            self.existentes = _listar_existentes(self.config, self.autor, self.novel_title)
        except RuntimeError as exc:
            self._erro_preparo = f"servidor do Audiobookshelf indisponivel: {exc}"
            log("Preparo", f"ERRO: {self._erro_preparo}")

    def _descartar_incompletos_da_espera(self):
        fila = pasta_espera(self.config)
        if not os.path.isdir(fila):
            return
        destino = os.path.join(PASTA_TRABALHO, "descartados")
        for raiz, _, nomes in os.walk(fila):
            for nome in nomes:
                caminho = os.path.join(raiz, nome)
                if nome.endswith(".part"):
                    os.remove(caminho)
                    log("Preparo", f"removido arquivo temporario incompleto: {nome}")
                elif nome.endswith(f".{self.config['formato']}") and duracao_arquivo(caminho) < 30:
                    os.makedirs(destino, exist_ok=True)
                    os.replace(caminho, os.path.join(destino, nome))
                    log("Preparo", f"audio incompleto na pasta de espera movido para {destino}: {nome}")

    def _avisar_fila_sem_destino(self):
        if not os.path.isdir(PASTA_ESPERA) or pasta_espera(self.config) == PASTA_ESPERA:
            return
        antigos = [n for n in os.listdir(PASTA_ESPERA) if not re.fullmatch(r".+_[0-9a-f]{10}", n)]
        if antigos:
            log("Preparo", f"AVISO: ha arquivos antigos na fila de envio sem destino definido "
                           f"({', '.join(antigos)} em {PASTA_ESPERA}); eles NAO foram enviados")

    def _validar_ambiente(self):
        cfg = self.config
        if not os.path.isfile(cfg["tts_python"]):
            return f"Python do TTS nao encontrado: {cfg['tts_python']}"
        if not os.path.isfile(os.path.join(cfg["xtts_model_dir"], "model.pth")):
            return f"Modelo XTTS nao encontrado em: {cfg['xtts_model_dir']}"
        if not os.path.isfile(os.path.join(cfg["voz_dir"], self.voz)):
            return f"Voz nao encontrada: {os.path.join(cfg['voz_dir'], self.voz)}"
        if shutil.which("ffmpeg") is None:
            return "ffmpeg nao encontrado no PATH"
        return verificar_destino(cfg)

    def _iniciar_pool(self):
        if self.pool is not None:
            self._ajustar_pool()
            return
        n, leitura = escolher_workers(self.config, 0)
        self._anunciar_workers(n, leitura)
        voz_path = os.path.join(self.config["voz_dir"], self.voz)
        pool = PoolTTS(self.config, [voz_path], workers=n, log_dir=os.path.join(PASTA_TRABALHO, "logs_tts"))
        log("XTTS", f"carregando o modelo em {pool.n_workers} worker(s) (cerca de 1 minuto)...")
        try:
            pool.iniciar()
        except Exception as exc:
            raise RuntimeError(f"falha ao iniciar o XTTS: {exc}") from exc
        log("XTTS", "modelo carregado e pronto")
        self.pool = pool

    def _ajustar_pool(self):
        n, leitura = escolher_workers(self.config, len(self.pool.workers))
        if n == len(self.pool.workers):
            return
        self._anunciar_workers(n, leitura)
        self.pool.redimensionar(n)

    def _anunciar_workers(self, n, leitura):
        if leitura:
            log("GPU", f"uso {leitura['util']}%, VRAM {leitura['usada_mb']}/{leitura['total_mb']} MB -> {n} worker(s) de XTTS")

    def _classificar(self, trechos, estado, pasta, volume, numero, usa_asr):
        reutilizados, para_verificar, a_gerar, sementes = {}, [], [], {}
        for trecho in trechos:
            anterior = estado.get(trecho["id"])
            wav = os.path.join(pasta, f"{trecho['id']}.wav")
            sementes[trecho["id"]] = zlib.crc32(
                f"{self.novel_title}:{volume}:{numero}:{trecho['id']}".encode("utf-8")
            ) % 1000000
            utilizavel = (anterior and anterior.get("texto") == trecho["texto"]
                          and anterior["res"].get("ok") and os.path.exists(wav))
            if utilizavel:
                reutilizados[trecho["id"]] = anterior["res"]
                if usa_asr and not anterior.get("verificado"):
                    para_verificar.append(trecho["id"])
            else:
                a_gerar.append(trecho)
        return reutilizados, para_verificar, a_gerar, sementes

    def _sintetizar(self, trechos, pasta, volume, numero):
        os.makedirs(pasta, exist_ok=True)
        caminho_estado = os.path.join(pasta, "estado.json")
        estado = {}
        if os.path.exists(caminho_estado):
            try:
                with open(caminho_estado, "r", encoding="utf-8") as arquivo:
                    estado = json.load(arquivo)
            except (OSError, ValueError):
                estado = {}

        usa_asr = bool(self.config["asr"])
        resultados, para_verificar, a_gerar, sementes = self._classificar(
            trechos, estado, pasta, volume, numero, usa_asr
        )
        jobs = [
            self._job(t, os.path.join(pasta, f"{t['id']}.wav"), sementes[t["id"]], 0) for t in a_gerar
        ]

        if resultados:
            sufixo = f" ({len(para_verificar)} deles so faltam a verificacao do Whisper)" if para_verificar else ""
            self._log(f"retomando: {len(resultados)} de {len(trechos)} trechos ja estavam gerados{sufixo}")
        if not jobs and not para_verificar:
            return resultados

        self._iniciar_pool()
        textos = {t["id"]: t["texto"] for t in trechos}
        trava = threading.Lock()

        def gravar_estado():
            _gravar_json_atomico(caminho_estado, estado)

        progresso = {"marco": 0}

        def ao_concluir(resposta, feitos, total):
            with trava:
                estado[resposta["id"]] = {"texto": textos[resposta["id"]], "res": resposta}
                gravar_estado()
                marco = (feitos * 4) // total
                avancou = marco > progresso["marco"]
                if avancou:
                    progresso["marco"] = marco
            if avancou:
                self._log(f"gerando trechos: {marco * 25}% ({feitos}/{total})")

        if jobs:
            novos = self.pool.gerar(jobs, ao_concluir=ao_concluir)
            resultados.update(novos)
        if usa_asr:
            self._verificar_asr(
                [j["id"] for j in jobs] + para_verificar, textos, pasta, sementes, resultados, estado,
                gravar_estado,
            )
        return resultados

    def _job(self, trecho, wav, semente, rodada):
        finais = [self.config["final_trecho"], "sem", self.config["final_trecho"]]
        job = {
            "id": trecho["id"],
            "texto": ajustar_final(trecho["texto"], finais[rodada % len(finais)]),
            "saida": os.path.abspath(wav),
            "seed": semente + rodada * 104729,
            "tentativas": int(self.config["qa_tentativas"]),
        }
        variacao = self.config["asr_variacao_params"].get(str(rodada))
        if variacao:
            job["params"] = variacao
        return job

    def _verificar_asr(self, ids, textos, pasta, sementes, resultados, estado, gravar_estado):
        pendentes = list(ids)
        rodadas = int(self.config["asr_rodadas"])
        por_id = {}
        cortadas = 0
        aparar = self.config.get("aparar_rajada_final", True)
        self._log(f"verificando {len(pendentes)} trechos com o Whisper...")
        for rodada in range(rodadas + 1):
            transcricoes = self.pool.transcrever(
                [(i, os.path.join(pasta, f"{i}.wav")) for i in pendentes]
            )
            ruins = []
            for i in pendentes:
                analise = analisar(textos[i], transcricoes[i], self.config.get("asr_limites"))
                por_id[i] = analise
                resultados[i]["asr_motivos"] = analise["motivos"]
                estado[i]["res"] = resultados[i]
                estado[i]["verificado"] = True
                if not analise["ok"]:
                    ruins.append(i)
            gravar_estado()
            if aparar:
                cortadas += self._aparar_rajadas(pendentes, textos, pasta)
            if not ruins or rodada == rodadas:
                break
            self._log(f"correcao {rodada + 1} de {rodadas}: refazendo {len(ruins)} trecho(s) que o Whisper reprovou")
            trechos_por_id = {i: {"id": i, "texto": textos[i]} for i in ruins}
            jobs = [
                self._job(trechos_por_id[i], os.path.join(pasta, f"{i}.wav"), sementes[i], rodada + 1)
                for i in ruins
            ]
            novos = self.pool.gerar(jobs)
            for i, res in novos.items():
                resultados[i] = res
                estado[i]["res"] = res
                estado[i]["verificado"] = False
            pendentes = ruins
        if cortadas:
            self._log(f"ruido apos o fim da frase removido em {cortadas} trecho(s)")
        restantes = [i for i, a in por_id.items() if not a["ok"]]
        if restantes:
            self._log(f"{len(restantes)} trecho(s) continuam com alerta apos {rodadas} correcao(oes) (detalhes no relatorio)")

    def _aparar_rajadas(self, ids, textos, pasta):
        candidatas = {}
        for i in ids:
            caminho = os.path.join(pasta, f"{i}.wav")
            wav, taxa = sf.read(caminho, dtype="float32")
            rajada = detectar_rajada_final(wav, taxa)
            if rajada:
                candidatas[i] = (caminho, wav, taxa, rajada)
        if not candidatas:
            return 0
        clipes = []
        for i, (caminho, wav, taxa, rajada) in candidatas.items():
            clipe = os.path.join(pasta, f"{i}_cauda.wav")
            sf.write(clipe, recortar(wav, rajada["ini"], rajada["fim"], taxa), taxa, subtype="PCM_16")
            clipes.append((i, clipe))
        transcricoes = self.pool.transcrever(clipes)
        cortadas = 0
        for i, (caminho, wav, taxa, rajada) in candidatas.items():
            esperada = palavras(textos[i])
            ultima = esperada[-1] if esperada else ""
            if ultima and tem_palavra_parecida(transcricoes[i], ultima):
                continue
            sf.write(caminho, cortar_em(wav, rajada["corte"], taxa), taxa, subtype="PCM_16")
            cortadas += 1
        for _, clipe in clipes:
            os.remove(clipe)
        return cortadas

    def _gravar_relatorio_qa(self, volume, numero, trechos, resultados):
        pasta = os.path.join(PASTA_TRABALHO, "qa", _nome_seguro(self.novel_title))
        os.makedirs(pasta, exist_ok=True)
        textos = {t["id"]: t["texto"] for t in trechos}
        alertas = [
            {"id": i, "texto": textos.get(i, ""), "motivos": r.get("motivos"),
             "asr_motivos": r.get("asr_motivos"),
             "tentativas": r.get("tentativas"), "metricas": r.get("metricas")}
            for i, r in sorted(resultados.items()) if not r.get("ok") or r.get("asr_motivos")
        ]
        nome = f"vol{volume}_cap{numero:04d}.json" if volume else f"cap{numero:04d}.json"
        caminho = os.path.join(pasta, nome)
        with open(caminho, "w", encoding="utf-8") as arquivo:
            json.dump({"trechos": len(trechos), "alertas": alertas}, arquivo, ensure_ascii=False, indent=1)
        return caminho

    def _metadados(self, cap, titulo_cap, volume, numero):
        if cap.get("livro"):
            return {
                "title": cap["titulo"],
                "artist": self.autor,
                "album_artist": self.autor,
                "album": self.novel_title,
                "track": str(numero),
                "genre": "Audiobook",
                "language": self.config["idioma"],
            }
        album = self.config["titulo_volume"].format(novel=self.novel_title, volume=volume)
        meta = {
            "title": titulo_cap,
            "artist": self.autor,
            "album_artist": self.autor,
            "album": album,
            "track": str(numero),
            "genre": "Audiobook",
            "series": self.novel_title,
            "language": self.config["idioma"],
        }
        if volume.isdigit():
            meta["series-part"] = volume
        return meta

    def _garantir_capa(self, cap):
        pasta = caminho_relativo(self.config, self.autor, self.novel_title, cap)[:-1]
        if pasta in self.volumes_com_capa:
            return
        self.volumes_com_capa.add(pasta)
        info = self.info or {}
        dados = info.get("capa_dados")
        partes_capa = pasta + (f"cover.{info.get('capa_extensao', 'jpg')}" if dados else "cover.jpg",)
        if partes_capa in self.existentes or not (dados or info.get("capa")):
            return
        capa = os.path.join(raiz_publicacao(self.config, pasta_espera(self.config)), *partes_capa)
        os.makedirs(os.path.dirname(capa), exist_ok=True)
        if dados:
            with open(capa, "wb") as arquivo:
                arquivo.write(dados)
        else:
            baixar_capa(info["capa"], capa)

    def _anexar_epub_original(self, cap):
        origem = (self.info or {}).get("epub_origem")
        if not origem or getattr(self, "_epub_anexado", False):
            return
        self._epub_anexado = True
        partes = caminho_relativo(self.config, self.autor, self.novel_title, cap)[:-1] + (
            _nome_seguro(f"{self.novel_title}.epub"),
        )
        if partes in self.existentes:
            return
        destino = os.path.join(raiz_publicacao(self.config, pasta_espera(self.config)), *partes)
        os.makedirs(os.path.dirname(destino), exist_ok=True)
        shutil.copyfile(origem, destino)

    def _publicar_lote(self):
        if not self.publicados:
            return
        publicados, self.publicados = self.publicados, []
        log("Envio", f"enviando {len(publicados)} capitulo(s) concluido(s) ({resumir_capitulos(publicados)}) "
                     "ao servidor...")
        for volume in sorted({str(cap["volume"]).strip() for cap in publicados if not cap.get("livro")}):
            self._gerar_epub_volume(volume)
        livros = [cap for cap in publicados if cap.get("livro")]
        if livros:
            self._anexar_epub_original(livros[0])
        if modo_remoto(self.config):
            try:
                enviados = enviar_espera(self.config, pasta_espera(self.config))
                log("Envio", f"OK: {enviados} arquivo(s) enviados a {self.config['abs_ssh']} (audios, capa e EPUB)")
            except RuntimeError as exc:
                log("Envio", f"FALHA ao enviar ({exc}); os audios continuam na pasta de espera e serao "
                             "reenviados na proxima execucao")
                return
        else:
            log("Envio", f"OK: {len(publicados)} capitulo(s) gravados em {self.config['abs_dir']}")
        escanear_biblioteca(self.config)

    def _pasta_textos(self, volume):
        return os.path.join(PASTA_TRABALHO, "textos", _nome_seguro(self.novel_title), f"vol_{volume}")

    def _guardar_texto(self, volume, numero, titulo_cap, paragrafos):
        pasta = self._pasta_textos(volume)
        os.makedirs(pasta, exist_ok=True)
        with open(os.path.join(pasta, f"{numero:04d}.json"), "w", encoding="utf-8") as arquivo:
            json.dump({"titulo": titulo_cap, "paragrafos": list(paragrafos)}, arquivo, ensure_ascii=False)

    def _gerar_epub_volume(self, volume):
        pasta = self._pasta_textos(volume)
        if not os.path.isdir(pasta):
            return
        capitulos = []
        for nome in sorted(os.listdir(pasta)):
            with open(os.path.join(pasta, nome), "r", encoding="utf-8") as arquivo:
                dados = json.load(arquivo)
            capitulos.append((dados["titulo"], dados["paragrafos"]))
        if not capitulos:
            return
        partes = caminho_epub_volume(self.config, self.autor, self.novel_title, volume)
        destino = os.path.join(raiz_publicacao(self.config, pasta_espera(self.config)), *partes)
        os.makedirs(os.path.dirname(destino), exist_ok=True)
        titulo_vol = self.config["titulo_volume"].format(novel=self.novel_title, volume=volume)
        criar_epub_volume(
            destino, titulo_vol, capitulos, autor=self.autor, serie=self.novel_title,
            indice_serie=volume if volume.isdigit() else None, idioma="pt",
        )


def _gravar_json_atomico(caminho, dados):
    temporario = caminho + ".tmp"
    with open(temporario, "w", encoding="utf-8") as arquivo:
        json.dump(dados, arquivo, ensure_ascii=False)
    os.replace(temporario, caminho)


def _listar_existentes(config, autor, novel_title):
    partes_base = (_nome_seguro(autor), _nome_seguro(novel_title))
    return listar_existentes(config, partes_base, pasta_espera(config))


def _nome_seguro(texto):
    texto = re.sub(r'[<>:"/\\|?*]', "", str(texto))
    return " ".join(texto.split()).rstrip(". ")


__all__ = ["SessaoAudiolivro", "separar_pendentes", "escanear_biblioteca"]
