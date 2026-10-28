import argparse
import json
import os
import sys
import time

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, RAIZ)

import soundfile as sf
import torch

from ab_texto import escrever_html
from centralnovel.audiolivro_config import carregar_config
from centralnovel.tts import compat, qa, verificar
from centralnovel.tts.engine_worker import _carregar_modelo, _gerar_uma_vez
from centralnovel.tts.normalizar import ajustar_final, normalizar

WAVS = os.path.join(RAIZ, "audiobook_work", "finetune", "prep", "dataset_v2", "wavs")

EMOCOES = {
    "alegre": {
        "clipes": ["026_6k2WVjD8Qcc_0054", "022_kVWvYDBYjcY_0030", "077_3qGacdvgKjI_0033"],
        "params": {"speed": 1.15, "temperature": 0.7, "top_p": 0.85},
        "frase": "Conseguimos! Eu não acredito que finalmente conseguimos!",
    },
    "raiva": {
        "clipes": ["034_xMZ_vAny6QM_0016", "092_KePwh4xyiww_0014", "070__ulSo3P9fs0_0021"],
        "params": {"speed": 1.15, "temperature": 0.65},
        "frase": "Saia daqui agora! Eu não vou repetir!",
    },
    "duvida": {
        "clipes": ["087_eTLA_hxrRto_0028", "077_3qGacdvgKjI_0038", "042_PfIH7HOw0uc_0022"],
        "params": {"speed": 1.0, "temperature": 0.7, "top_p": 0.85},
        "frase": "Será que ele realmente vai voltar... ou foi tudo uma mentira?",
    },
    "suave": {
        "clipes": ["093_BsfKY2KegzY_0012", "065_inEH83XY60o_0043", "064_IogAh39-aGs_0068"],
        "params": {"speed": 1.0, "temperature": 0.55},
        "frase": "Fique quieto. Tem alguém do outro lado da porta.",
    },
}

NEUTRA = "O sol se pôs atrás das montanhas, e a vila mergulhou em silêncio."


def latents(modelo, wavs, ref):
    return modelo.get_conditioning_latents(
        audio_path=wavs, load_sr=24000, sound_norm_refs=True,
        gpt_cond_len=ref["gpt_cond_len"], gpt_cond_chunk_len=ref["gpt_cond_chunk_len"],
        max_ref_length=ref["max_ref_length"],
    )


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--saida", default=os.path.join(RAIZ, "audiobook_work", "emocao_lab"))
    p.add_argument("--seed", type=int, default=0)
    args = p.parse_args()
    os.makedirs(args.saida, exist_ok=True)

    compat.aplicar()
    config = carregar_config()
    base = dict(config["xtts"])
    modelo = _carregar_modelo(config["xtts_model_dir"], "cuda")
    gpt_n, spk_n = latents(modelo, [os.path.join(config["voz_dir"], config["voz_padrao"])], config["xtts_ref"])

    def texto_final(t):
        return ajustar_final(normalizar(t, config.get("normalizacao")), config["final_trecho"])

    jobs = [("neutro", "atual (referencia padrao)", NEUTRA, gpt_n, base)]
    for nome, e in EMOCOES.items():
        gpt_e, _ = latents(modelo, [os.path.join(WAVS, c + ".wav") for c in e["clipes"]], config["xtts_ref"])
        perfil = {**base, **e["params"]}
        jobs += [
            (nome, "0 atual (referencia padrao)", e["frase"], gpt_n, base),
            (nome, "1 so parametros", e["frase"], gpt_n, perfil),
            (nome, "2 mistura 30%", e["frase"], 0.7 * gpt_n + 0.3 * gpt_e, base),
            (nome, "3 mistura 60%", e["frase"], 0.4 * gpt_n + 0.6 * gpt_e, base),
            (nome, "4 latente 100%", e["frase"], gpt_e, base),
            (nome, "5 mistura 60% + parametros", e["frase"], 0.4 * gpt_n + 0.6 * gpt_e, perfil),
        ]

    from transformers import pipeline
    asr = pipeline("automatic-speech-recognition", model=config["asr_modelo"], device="cuda", dtype=torch.float16)

    itens = []
    for i, (caso, variante, frase, gpt, params) in enumerate(jobs):
        t = time.time()
        texto = texto_final(frase)
        wav = _gerar_uma_vez(modelo, gpt, spk_n, texto, config["idioma_xtts"], params, args.seed)
        arquivo = f"{caso}_{variante.split()[0]}.wav"
        sf.write(os.path.join(args.saida, arquivo), wav, 24000, subtype="PCM_16")
        ouvido = asr({"raw": wav, "sampling_rate": 24000},
                     generate_kwargs={"language": "portuguese", "task": "transcribe"})["text"].strip()
        check = verificar.analisar(frase, ouvido)
        metricas = qa.medir(wav, 24000, texto)
        itens.append({"caso": caso, "variante": f"{variante} | asr {check['cobertura']} {' '.join(check['motivos'])}",
                      "texto": f"{frase}\nouvido: {ouvido}\nparams: {json.dumps(params)}",
                      "arquivo": arquivo, "metricas": metricas})
        print(f"{i + 1}/{len(jobs)} {caso} {variante} {time.time() - t:.1f}s cobertura={check['cobertura']} {check['motivos']}", flush=True)

    casos = [{"nome": n, "titulo": n} for n in ["neutro", *EMOCOES]]
    print(escrever_html(casos, itens, args.saida))


if __name__ == "__main__":
    main()
