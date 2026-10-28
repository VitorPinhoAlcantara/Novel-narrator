import argparse
import collections
import json
import os
import re
import sys
import time

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, RAIZ)

import soundfile as sf
import torch

from centralnovel.audiolivro_config import carregar_config
from centralnovel.tts import compat, qa
from centralnovel.tts.dividir import dividir_paragrafos
from centralnovel.tts.engine_worker import _carregar_modelo, _gerar_uma_vez
from centralnovel.tts.montar import codificar_m4b, cortar_em, detectar_rajada_final, montar_capitulo, recortar
from centralnovel.tts.normalizar import ajustar_final, normalizar
from centralnovel.tts.verificar import analisar, palavras, tem_palavra_parecida
from emocao_lab import EMOCOES, WAVS, latents

_RAIVA = re.compile(r"\b(merda|maldit\w*|bastard\w*|droga|cale|saia|morr\w*|idiota|desgraça\w*|inferno|nunca)\b", re.I)


def emocao(texto):
    if "?" in texto:
        return "duvida"
    if "!" in texto:
        return "raiva" if _RAIVA.search(texto) else "alegre"
    return "neutro"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--texto", default=os.path.join(RAIZ, "audiobook_work", "textos", "Shadow Slave", "vol_5", "0854.json"))
    p.add_argument("--saida", default=os.path.join(RAIZ, "audiobook_work", "emocao_lab", "cap_0854_B"))
    args = p.parse_args()
    os.makedirs(args.saida, exist_ok=True)

    compat.aplicar()
    config = carregar_config()
    with open(args.texto, encoding="utf-8") as f:
        cap = json.load(f)
    opcoes = config.get("normalizacao", {})
    textos = [normalizar(t, opcoes) for t in [cap["titulo"], *cap["paragrafos"]]]
    pausas = config["pausas_ms"]
    trechos = dividir_paragrafos([t for t in textos if t], pausa_frase_ms=pausas["frase"],
                                 pausa_paragrafo_ms=pausas["paragrafo"])

    modelo = _carregar_modelo(config["xtts_model_dir"], "cuda")
    ref = config["xtts_ref"]
    gpt_n, spk = latents(modelo, [os.path.join(config["voz_dir"], config["voz_padrao"])], ref)
    gpts = {"neutro": gpt_n}
    for nome in ("alegre", "raiva", "duvida"):
        gpts[nome] = latents(modelo, [os.path.join(WAVS, c + ".wav") for c in EMOCOES[nome]["clipes"]], ref)[0]

    from transformers import pipeline
    asr = pipeline("automatic-speech-recognition", model=config["asr_modelo"], device="cuda", dtype=torch.float16)

    def ouvir(wav):
        return asr({"raw": wav, "sampling_rate": 24000},
                   generate_kwargs={"language": "portuguese", "task": "transcribe"})["text"].strip()

    finais = [config["final_trecho"], "sem", config["final_trecho"]]
    tentativas = int(config["qa_tentativas"])
    contagem = collections.Counter()
    alertas = []
    inicio = time.time()
    for n, trecho in enumerate(trechos):
        emo = emocao(trecho["texto"])
        contagem[emo] += 1
        semente = int(trecho["id"])
        for rodada in range(int(config["asr_rodadas"]) + 1):
            params = {**config["xtts"], **(config["asr_variacao_params"].get(str(rodada)) or {})}
            texto = ajustar_final(trecho["texto"], finais[rodada % len(finais)])
            melhor = None
            for k in range(tentativas):
                wav = _gerar_uma_vez(modelo, gpts[emo], spk, texto, config["idioma_xtts"], params,
                                     semente + rodada * 104729 + k * 7919)
                metricas = qa.medir(wav, 24000, texto)
                aprovado, _ = qa.avaliar(metricas, config["qa"])
                ponto = qa.pontuacao(metricas, config["qa"])
                if melhor is None or ponto < melhor[0]:
                    melhor = (ponto, wav)
                if aprovado:
                    break
            wav = melhor[1]
            analise = analisar(trecho["texto"], ouvir(wav), config.get("asr_limites"))
            if analise["ok"]:
                break
        if not analise["ok"]:
            alertas.append((trecho["id"], analise["motivos"], trecho["texto"][:80]))
        rajada = detectar_rajada_final(wav, 24000) if config.get("aparar_rajada_final", True) else None
        if rajada:
            esperada = palavras(trecho["texto"])
            cauda = ouvir(recortar(wav, rajada["ini"], rajada["fim"], 24000))
            if not (esperada and tem_palavra_parecida(cauda, esperada[-1])):
                wav = cortar_em(wav, rajada["corte"], 24000)
        sf.write(os.path.join(args.saida, f"{trecho['id']}.wav"), wav, 24000, subtype="PCM_16")
        print(f"{n + 1}/{len(trechos)} {emo:7s} {trecho['texto'][:70]}", flush=True)

    wav_cap = os.path.join(args.saida, "capitulo.wav")
    duracao = montar_capitulo(trechos, args.saida, wav_cap)
    destino = args.saida + ".m4b"
    codificar_m4b(wav_cap, destino, {"title": f"{cap['titulo']} (B emocao)"}, bitrate=config["bitrate"])
    print(f"pronto: {destino} | {duracao / 60:.1f} min em {(time.time() - inicio) / 60:.1f} min | {dict(contagem)}")
    for a in alertas:
        print("alerta", *a)


if __name__ == "__main__":
    main()
