import posixpath
import subprocess

import requests

from .servidor_abs import _ssh

_COMANDO_MONTAGENS = (
    "for n in $(docker ps --format '{{.Names}} {{.Image}}' | awk '/audiobookshelf/ {print $1}'); do "
    "docker inspect \"$n\" --format '{{range .Mounts}}{{.Source}}|{{.Destination}}{{println}}{{end}}'; done"
)


def listar_bibliotecas(config, url=None, token=None, timeout=15):
    url = (url or config["abs_url"]).rstrip("/")
    token = token if token is not None else config.get("abs_token", "")
    resposta = requests.get(f"{url}/api/libraries", headers={"Authorization": f"Bearer {token}"}, timeout=timeout)
    resposta.raise_for_status()
    return [
        {
            "id": b["id"],
            "nome": b["name"],
            "tipo": b.get("mediaType", ""),
            "pastas": [p.get("fullPath") for p in b.get("folders", []) if p.get("fullPath")],
        }
        for b in resposta.json().get("libraries", [])
    ]


def detectar_mapeamento(config):
    try:
        resultado = _ssh(config, _COMANDO_MONTAGENS, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return {}
    mapa = {}
    for linha in resultado.stdout.splitlines():
        if "|" in linha:
            origem, destino = linha.split("|", 1)
            if origem.strip() and destino.strip():
                mapa[destino.strip()] = origem.strip()
    return mapa


def pasta_no_servidor(caminho_container, mapa):
    melhor = None
    for destino in mapa:
        raiz = destino.rstrip("/")
        if caminho_container == raiz or caminho_container.startswith(raiz + "/"):
            if melhor is None or len(raiz) > len(melhor.rstrip("/")):
                melhor = destino
    if melhor is None:
        return None
    resto = caminho_container[len(melhor.rstrip("/")):].lstrip("/")
    return posixpath.join(mapa[melhor], resto) if resto else mapa[melhor]


def bibliotecas_na_mesma_pasta(bibliotecas, alvo, pasta):
    raiz = pasta.rstrip("/")
    outras = []
    for b in bibliotecas:
        if b["id"] == alvo["id"]:
            continue
        for outra in b["pastas"]:
            o = outra.rstrip("/")
            if o == raiz or o.startswith(raiz + "/") or raiz.startswith(o + "/"):
                outras.append(b["nome"])
                break
    return outras
