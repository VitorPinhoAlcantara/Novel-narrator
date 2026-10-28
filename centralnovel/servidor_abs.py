"""Destino dos audios: pasta local do Audiobookshelf ou servidor remoto via SSH.

Com `abs_ssh` configurado (ex.: "topiinho@server"), os audios sao montados numa pasta de
espera local e enviados por SSH (tar em fluxo, sem depender de rsync) para `abs_remote_dir`.
So depois de conferir os tamanhos no servidor a copia local e apagada.
"""

import os
import posixpath
import shlex
import shutil
import subprocess
import tarfile

SSH_BASE = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]


def modo_remoto(config):
    return bool(config.get("abs_ssh"))


def raiz_publicacao(config, pasta_espera):
    """Pasta local onde os audios sao gravados (a de espera, se o destino for remoto)."""
    return pasta_espera if modo_remoto(config) else config["abs_dir"]


def verificar_destino(config):
    """Retorna None se o destino esta acessivel, ou a mensagem de erro."""
    if not modo_remoto(config):
        if not os.path.isdir(config["abs_dir"]):
            return f"Pasta do Audiobookshelf nao encontrada: {config['abs_dir']}"
        return None
    try:
        resultado = _ssh(config, f"test -d {shlex.quote(config['abs_remote_dir'])}", timeout=30)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return f"Nao foi possivel conectar por SSH a {config['abs_ssh']}: {exc}"
    if resultado.returncode == 255:
        return f"Nao foi possivel conectar por SSH a {config['abs_ssh']}: {resultado.stderr.strip()}"
    if resultado.returncode != 0:
        return f"Pasta remota nao encontrada: {config['abs_remote_dir']}"
    return None


def listar_existentes(config, partes_base, pasta_espera):
    """Conjunto de caminhos relativos (tuplas) ja publicados sob `partes_base` (autor, novel),
    incluindo o que ainda aguarda envio na pasta de espera.

    Levanta RuntimeError se o servidor nao puder ser consultado.
    """
    existentes = set()
    if modo_remoto(config):
        base = posixpath.join(config["abs_remote_dir"], *partes_base)
        comando = f"find {shlex.quote(base)} -type f -printf '%P\\n' 2>/dev/null; true"
        try:
            resultado = _ssh(config, comando, timeout=60)
        except (subprocess.TimeoutExpired, OSError) as exc:
            raise RuntimeError(f"nao foi possivel consultar o servidor: {exc}") from exc
        if resultado.returncode == 255:
            raise RuntimeError(f"nao foi possivel consultar o servidor: {resultado.stderr.strip()}")
        for linha in resultado.stdout.splitlines():
            if linha:
                existentes.add(tuple(partes_base) + tuple(linha.split("/")))
        raiz_local = pasta_espera
    else:
        raiz_local = config["abs_dir"]

    base_local = os.path.join(raiz_local, *partes_base)
    for pasta, _, arquivos in os.walk(base_local):
        rel = os.path.relpath(pasta, base_local)
        subpartes = () if rel == "." else tuple(rel.split(os.sep))
        for nome in arquivos:
            existentes.add(tuple(partes_base) + subpartes + (nome,))
    return existentes


def enviar_espera(config, pasta_espera):
    """Envia tudo o que esta na pasta de espera para o servidor e a esvazia.

    Retorna a quantidade de arquivos enviados. Levanta RuntimeError em caso de falha
    (a pasta de espera e preservada).
    """
    if not modo_remoto(config) or not os.path.isdir(pasta_espera):
        return 0
    arquivos = _arquivos_locais(pasta_espera)
    if not arquivos:
        return 0

    destino = shlex.quote(config["abs_remote_dir"])
    comando = SSH_BASE + [config["abs_ssh"], f"mkdir -p {destino} && tar -C {destino} -xf -"]
    with subprocess.Popen(comando, stdin=subprocess.PIPE, stderr=subprocess.PIPE) as proc:
        try:
            with tarfile.open(fileobj=proc.stdin, mode="w|", format=tarfile.PAX_FORMAT,
                              encoding="utf-8") as pacote:
                for nome in sorted(os.listdir(pasta_espera)):
                    pacote.add(os.path.join(pasta_espera, nome), arcname=nome, filter=_sem_temporarios)
        except (OSError, BrokenPipeError):
            pass  # o erro real vem do codigo de saida e do stderr do ssh
        finally:
            try:
                proc.stdin.close()
            except OSError:
                pass
        erro = proc.stderr.read().decode("utf-8", errors="replace").strip()
        codigo = proc.wait()
    if codigo != 0:
        raise RuntimeError(f"ssh/tar terminou com codigo {codigo}: {erro[:300]}")

    faltando = _conferir_tamanhos(config, arquivos)
    if faltando:
        raise RuntimeError(f"conferencia de tamanho falhou no servidor: {', '.join(faltando[:3])}")

    shutil.rmtree(pasta_espera, ignore_errors=True)
    return len(arquivos)


def _arquivos_locais(pasta):
    """{caminho relativo com '/': tamanho} de todos os arquivos da pasta."""
    arquivos = {}
    for raiz, _, nomes in os.walk(pasta):
        for nome in nomes:
            if nome.endswith(".part"):
                continue
            caminho = os.path.join(raiz, nome)
            rel = os.path.relpath(caminho, pasta).replace(os.sep, "/")
            arquivos[rel] = os.path.getsize(caminho)
    return arquivos


def _sem_temporarios(info):
    return None if info.name.endswith(".part") else info


def _conferir_tamanhos(config, arquivos):
    """Retorna a lista de arquivos ausentes ou com tamanho diferente no servidor."""
    topos = sorted({rel.split("/")[0] for rel in arquivos})
    caminhos = " ".join(shlex.quote(posixpath.join(config["abs_remote_dir"], topo)) for topo in topos)
    resultado = _ssh(config, f"find {caminhos} -type f -printf '%s\\t%p\\n' 2>/dev/null; true", timeout=120)
    remotos = {}
    for linha in resultado.stdout.splitlines():
        tamanho, _, caminho = linha.partition("\t")
        if tamanho.isdigit():
            remotos[caminho] = int(tamanho)

    return [
        rel for rel, tamanho in arquivos.items()
        if remotos.get(posixpath.join(config["abs_remote_dir"], rel)) != tamanho
    ]


def _ssh(config, comando, timeout):
    return subprocess.run(
        SSH_BASE + [config["abs_ssh"], comando],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
    )
