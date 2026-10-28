import os
import subprocess

import numpy as np
import soundfile as sf

TAXA = 24000
_LIMIAR_BORDA_DB = -48.0
_MARGEM_BORDA_S = 0.04
_JANELA_S = 0.01


def aparar_bordas(wav, taxa=TAXA):
    janela = max(1, int(taxa * _JANELA_S))
    quadros = len(wav) // janela
    if quadros == 0:
        return wav
    rms = np.sqrt(np.mean(wav[: quadros * janela].reshape(quadros, janela) ** 2, axis=1))
    ativos = np.flatnonzero(20.0 * np.log10(np.maximum(rms, 1e-6)) > _LIMIAR_BORDA_DB)
    if len(ativos) == 0:
        return wav
    margem = int(taxa * _MARGEM_BORDA_S)
    inicio = max(0, ativos[0] * janela - margem)
    fim = min(len(wav), (ativos[-1] + 1) * janela + margem)
    return wav[inicio:fim]


def _ilhas(wav, taxa=TAXA, limiar_db=-50.0, junta_s=0.04):
    n = max(1, int(taxa * _JANELA_S))
    q = len(wav) // n
    if q == 0:
        return []
    rms = np.sqrt(np.mean(wav[: q * n].reshape(q, n) ** 2, axis=1))
    ativo = 20.0 * np.log10(np.maximum(rms, 1e-7)) > limiar_db
    ilhas = []
    i = 0
    while i < q:
        if ativo[i]:
            j = i
            while j < q and ativo[j]:
                j += 1
            if ilhas and (i - ilhas[-1][1]) * _JANELA_S < junta_s:
                ilhas[-1][1] = j
            else:
                ilhas.append([i, j])
            i = j
        else:
            i += 1
    return [(a * _JANELA_S, b * _JANELA_S) for a, b in ilhas]


def detectar_rajada_final(wav, taxa=TAXA, gap_min_s=0.6, rajada_max_s=0.4, anterior_min_s=0.3, sobra_s=0.25):
    ilhas = _ilhas(wav, taxa)
    if len(ilhas) < 2:
        return None
    ini, fim = ilhas[-1]
    anterior_ini, anterior_fim = ilhas[-2]
    if ini - anterior_fim >= gap_min_s and (fim - ini) <= rajada_max_s and (anterior_fim - anterior_ini) >= anterior_min_s:
        return {"ini": ini, "fim": fim, "corte": anterior_fim + sobra_s}
    return None


def cortar_em(wav, corte_s, taxa=TAXA, fade_s=0.03):
    corte = int(corte_s * taxa)
    if corte >= len(wav):
        return wav
    saida = wav[:corte].copy()
    f = min(int(fade_s * taxa), len(saida))
    if f > 0:
        saida[-f:] *= np.linspace(1.0, 0.0, f, dtype=np.float32)
    return saida


def recortar(wav, ini_s, fim_s, taxa=TAXA, margem_s=0.15):
    a = max(0, int((ini_s - margem_s) * taxa))
    b = min(len(wav), int((fim_s + margem_s) * taxa))
    return wav[a:b]


def montar_capitulo(trechos, pasta_wavs, saida_wav, pausa_inicial_ms=600, pausa_final_ms=1200):
    partes = [np.zeros(int(TAXA * pausa_inicial_ms / 1000), dtype=np.float32)]
    for trecho in trechos:
        caminho = os.path.join(pasta_wavs, f"{trecho['id']}.wav")
        wav, taxa = sf.read(caminho, dtype="float32")
        if wav.ndim > 1:
            wav = wav.mean(axis=1)
        if taxa != TAXA:
            raise RuntimeError(f"taxa inesperada em {caminho}: {taxa}")
        partes.append(aparar_bordas(wav))
        partes.append(np.zeros(int(TAXA * trecho["pausa_ms"] / 1000), dtype=np.float32))
    partes.append(np.zeros(int(TAXA * pausa_final_ms / 1000), dtype=np.float32))
    audio = np.concatenate(partes)
    os.makedirs(os.path.dirname(os.path.abspath(saida_wav)), exist_ok=True)
    sf.write(saida_wav, audio, TAXA, subtype="PCM_16")
    return len(audio) / TAXA


def duracao_arquivo(caminho):
    resultado = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", caminho],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    try:
        return float(resultado.stdout.strip())
    except ValueError:
        return 0.0


def codificar_m4b(wav, destino, metadados, bitrate="96k", loudnorm=True):
    os.makedirs(os.path.dirname(os.path.abspath(destino)), exist_ok=True)
    temporario = destino + ".part"
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", wav, "-vn"]
    if loudnorm:
        cmd += ["-af", "loudnorm=I=-18:TP=-1.5:LRA=11"]
    cmd += ["-ac", "1", "-ar", "44100", "-c:a", "aac", "-b:a", bitrate]
    for chave, valor in metadados.items():
        cmd += ["-metadata", f"{chave}={valor}"]
    cmd += ["-movflags", "+faststart+use_metadata_tags", "-f", "ipod", temporario]
    try:
        resultado = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if resultado.returncode != 0:
            raise RuntimeError(f"ffmpeg: {resultado.stderr.strip()[:300]}")
        if duracao_arquivo(temporario) <= 0:
            raise RuntimeError("ffmpeg gerou um arquivo sem duracao")
        os.replace(temporario, destino)
    finally:
        if os.path.exists(temporario):
            os.remove(temporario)
    return destino
