"""Configuracao do fluxo de audiolivro (XTTS + Audiobookshelf).

Os valores ficam em `audiobook_config.json` (criado na primeira execucao, fora do git,
porque guarda o token do Audiobookshelf). O token tambem pode vir da variavel ABS_TOKEN.
"""

import hashlib
import json
import os
import re

RAIZ_PROJETO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARQUIVO_CONFIG = "audiobook_config.json"
PASTA_TRABALHO = "audiobook_work"
PASTA_ESPERA = f"{PASTA_TRABALHO}/espera_envio"  # audios prontos aguardando envio ao servidor

PADRAO = {
    "voz_dir": os.path.join(RAIZ_PROJETO, "vozes"),
    "voz_padrao": "voz_shadow_slave_ref.wav",
    "idioma": "por",
    "dispositivo": "cuda",
    "formato": "m4b",
    "xtts": {
        "temperature": 0.55,
        "repetition_penalty": 2.0,
        "top_k": 30,
        "top_p": 0.7,
        "speed": 1.1,
    },
    "xtts_ref": {
        "gpt_cond_len": 30,
        "gpt_cond_chunk_len": 6,
        "max_ref_length": 30,
    },
    "tts_python": os.path.join(RAIZ_PROJETO, "tts_env", "Scripts", "python.exe"),
    "xtts_model_dir": os.path.join(RAIZ_PROJETO, "modelos", "xtts_narrador"),
    "idioma_xtts": "pt",
    "workers": 3,
    "workers_adaptativo": True,
    "gpu_util_baixo": 35,
    "gpu_util_moderado": 70,
    "vram_por_worker_mb": 3500,
    "vram_reserva_mb": 2000,
    "vram_critica_mb": 1000,
    "vram_folga_mb": 2500,
    "matmul": "highest",
    "qa": {},
    "qa_tentativas": 3,
    "final_trecho": "e2a",
    "asr": True,
    "asr_modelo": "openai/whisper-small",
    "asr_rodadas": 2,
    "aparar_rajada_final": True,
    "asr_variacao_params": {"2": {"temperature": 0.7, "top_p": 0.85}},
    "normalizacao": {},
    "bitrate": "96k",
    "pausas_ms": {"frase": 250, "paragrafo": 700},
    "latents_cache": f"{PASTA_TRABALHO}/latents",
    "lote_capitulos": 10,
    # Audiobookshelf
    # servidor remoto via SSH (deixe abs_ssh vazio para usar a pasta local abs_dir)
    "abs_ssh": "topiinho@server",
    "abs_remote_dir": "/home/topiinho/Code/production/audiobookshelf/audiobooks",
    "abs_dir": "",
    "abs_url": "http://192.168.15.3:13378",
    "abs_token": "",
    "abs_biblioteca": "",
    "titulo_volume": "{novel} Volume {volume}",
    "autor_padrao": "Central Novel",
}


def carregar_config():
    config = json.loads(json.dumps(PADRAO))  # copia profunda
    if os.path.exists(ARQUIVO_CONFIG):
        with open(ARQUIVO_CONFIG, "r", encoding="utf-8") as arquivo:
            _mesclar(config, json.load(arquivo))
    else:
        with open(ARQUIVO_CONFIG, "w", encoding="utf-8") as arquivo:
            json.dump(PADRAO, arquivo, indent=2, ensure_ascii=False)
        print(f"Configuracao criada: {ARQUIVO_CONFIG} (ajuste caminhos, voz e token se precisar)")

    token_env = os.environ.get("ABS_TOKEN", "").strip()
    if token_env:
        config["abs_token"] = token_env
    return config


def _mesclar(base, novo):
    for chave, valor in novo.items():
        if isinstance(valor, dict) and isinstance(base.get(chave), dict):
            _mesclar(base[chave], valor)
        else:
            base[chave] = valor


def salvar_config(alteracoes):
    dados = {}
    if os.path.exists(ARQUIVO_CONFIG):
        with open(ARQUIVO_CONFIG, "r", encoding="utf-8") as arquivo:
            dados = json.load(arquivo)
    dados.update(alteracoes)
    temporario = ARQUIVO_CONFIG + ".tmp"
    with open(temporario, "w", encoding="utf-8") as arquivo:
        json.dump(dados, arquivo, indent=2, ensure_ascii=False)
    os.replace(temporario, ARQUIVO_CONFIG)


def pasta_espera(config):
    if not config.get("abs_ssh"):
        return PASTA_ESPERA
    remota = str(config.get("abs_remote_dir", ""))
    nome = re.sub(r"[^A-Za-z0-9]+", "_", os.path.basename(remota.rstrip("/"))).strip("_") or "destino"
    assinatura = hashlib.sha1(f"{config['abs_ssh']}:{remota}".encode("utf-8")).hexdigest()[:10]
    return os.path.join(PASTA_ESPERA, f"{nome}_{assinatura}")
