"""Utilities for download workflow."""

import os
import re
from datetime import datetime

from .config import LOG_DIR


def limpar_nome_arquivo(texto):
    texto = re.sub(r'[<>:"/\|?*]', "", texto)
    texto = " ".join(texto.split())
    return texto.replace(" ", "_")


def criar_pasta_volume(volume):
    pasta = f"Lord Of The Mysteries Vol {volume.zfill(2)}"
    if not os.path.exists(pasta):
        os.makedirs(pasta)
        print(f"Pasta criada: {pasta}")
    return pasta


def resumir_capitulos(capitulos):
    """Ex.: 'Vol 1: 1-3, 7 | Vol 2: 10'."""
    por_volume = {}
    for cap in capitulos:
        por_volume.setdefault(str(cap["volume"]), set()).add(int(cap["capitulo"]))
    partes = [
        f"Vol {volume}: {_comprimir_intervalos(sorted(numeros))}" if volume.strip()
        else f"Caps {_comprimir_intervalos(sorted(numeros))}"
        for volume, numeros in por_volume.items()
    ]
    return " | ".join(partes)


def _comprimir_intervalos(numeros):
    faixas = []
    inicio = anterior = numeros[0]
    for numero in numeros[1:] + [None]:
        if numero is not None and numero == anterior + 1:
            anterior = numero
            continue
        faixas.append(str(inicio) if inicio == anterior else f"{inicio}-{anterior}")
        inicio = anterior = numero
    return ", ".join(faixas)


def gravar_log_falhas(falhas, novel_title, caminho=None):
    """Grava (ou reescreve) o log de falhas e retorna o caminho do arquivo."""
    if caminho is None:
        os.makedirs(LOG_DIR, exist_ok=True)
        nome = limpar_nome_arquivo(novel_title) or "novel"
        carimbo = datetime.now().strftime("%Y%m%d_%H%M%S")
        caminho = os.path.join(LOG_DIR, f"falhas_{nome}_{carimbo}.log")

    caps = [item["cap"] for item in falhas]
    numeros = sorted({int(cap["capitulo"]) for cap in caps})
    linhas = [
        f"Novel: {novel_title}",
        f"Gerado em: {datetime.now():%Y-%m-%d %H:%M:%S}",
        f"Capitulos com erro: {len(falhas)}",
        f"Resumo: {resumir_capitulos(caps)}",
        f"Para rebaixar (menu Capitulos especificos): {', '.join(map(str, numeros))}",
        "",
    ]
    for item in falhas:
        cap = item["cap"]
        linhas.append(
            f"Vol. {cap['volume']} | Cap. {cap['capitulo']} | {cap['titulo']} | {item['motivo']}"
        )
        linhas.append(f"    {cap['url']}")

    with open(caminho, "w", encoding="utf-8") as file_obj:
        file_obj.write("\n".join(linhas) + "\n")
    return caminho
