import hashlib
import os
import posixpath
import re
import shlex
import shutil
import subprocess
import tarfile

SSH_BASE = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]


def is_remote(abs_cfg):
    return bool(abs_cfg.get("ssh"))


def outbox_root(config):
    return os.path.join(config["paths"]["data_dir"], "outbox")


def outbox_dir(config):
    abs_cfg = config["abs"]
    root = outbox_root(config)
    if not is_remote(abs_cfg):
        return root
    remote = str(abs_cfg.get("remote_dir", ""))
    name = re.sub(r"[^A-Za-z0-9]+", "_", os.path.basename(remote.rstrip("/"))).strip("_") or "destino"
    signature = hashlib.sha1(f"{abs_cfg['ssh']}:{remote}".encode()).hexdigest()[:10]
    return os.path.join(root, f"{name}_{signature}")


def publish_root(config):
    return outbox_dir(config) if is_remote(config["abs"]) else config["abs"]["local_dir"]


def check_destination(abs_cfg):
    if not is_remote(abs_cfg):
        if not os.path.isdir(abs_cfg["local_dir"]):
            return f"Pasta do Audiobookshelf nao encontrada: {abs_cfg['local_dir']}"
        return None
    try:
        result = ssh(abs_cfg, f"test -d {shlex.quote(abs_cfg['remote_dir'])}", timeout=30)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return f"Nao foi possivel conectar por SSH a {abs_cfg['ssh']}: {exc}"
    if result.returncode == 255:
        return f"Nao foi possivel conectar por SSH a {abs_cfg['ssh']}: {result.stderr.strip()}"
    if result.returncode != 0:
        return f"Pasta remota nao encontrada: {abs_cfg['remote_dir']}"
    return None


def list_existing(config, base_parts):
    abs_cfg = config["abs"]
    existing = set()
    if is_remote(abs_cfg):
        base = posixpath.join(abs_cfg["remote_dir"], *base_parts)
        command = f"find {shlex.quote(base)} -type f -printf '%P\\n' 2>/dev/null; true"
        try:
            result = ssh(abs_cfg, command, timeout=60)
        except (subprocess.TimeoutExpired, OSError) as exc:
            raise RuntimeError(f"nao foi possivel consultar o servidor: {exc}") from exc
        if result.returncode == 255:
            raise RuntimeError(f"nao foi possivel consultar o servidor: {result.stderr.strip()}")
        for line in result.stdout.splitlines():
            if line:
                existing.add(tuple(base_parts) + tuple(line.split("/")))

    local_base = os.path.join(publish_root(config), *base_parts)
    for folder, _, files in os.walk(local_base):
        rel = os.path.relpath(folder, local_base)
        sub_parts = () if rel == "." else tuple(rel.split(os.sep))
        for name in files:
            existing.add(tuple(base_parts) + sub_parts + (name,))
    return existing


def send_outbox(config):
    abs_cfg = config["abs"]
    outbox = outbox_dir(config)
    if not is_remote(abs_cfg) or not os.path.isdir(outbox):
        return 0
    files = local_files(outbox)
    if not files:
        return 0

    destination = shlex.quote(abs_cfg["remote_dir"])
    command = SSH_BASE + [abs_cfg["ssh"], f"mkdir -p {destination} && tar -C {destination} -xf -"]
    with subprocess.Popen(command, stdin=subprocess.PIPE, stderr=subprocess.PIPE) as proc:
        try:
            with tarfile.open(fileobj=proc.stdin, mode="w|", format=tarfile.PAX_FORMAT, encoding="utf-8") as tar:
                for name in sorted(os.listdir(outbox)):
                    tar.add(os.path.join(outbox, name), arcname=name, filter=_skip_partial)
        except OSError:
            pass
        finally:
            try:
                proc.stdin.close()
            except OSError:
                pass
        error = proc.stderr.read().decode("utf-8", errors="replace").strip()
        code = proc.wait()
    if code != 0:
        raise RuntimeError(f"ssh/tar terminou com codigo {code}: {error[:300]}")

    missing = _check_sizes(abs_cfg, files)
    if missing:
        raise RuntimeError(f"conferencia de tamanho falhou no servidor: {', '.join(missing[:3])}")

    shutil.rmtree(outbox, ignore_errors=True)
    return len(files)


def local_files(folder):
    files = {}
    for root, _, names in os.walk(folder):
        for name in names:
            if name.endswith(".part"):
                continue
            path = os.path.join(root, name)
            files[os.path.relpath(path, folder).replace(os.sep, "/")] = os.path.getsize(path)
    return files


def ssh(abs_cfg, command, timeout):
    return subprocess.run(
        SSH_BASE + [abs_cfg["ssh"], command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def _skip_partial(info):
    return None if info.name.endswith(".part") else info


def _check_sizes(abs_cfg, files):
    tops = sorted({rel.split("/")[0] for rel in files})
    paths = " ".join(shlex.quote(posixpath.join(abs_cfg["remote_dir"], top)) for top in tops)
    result = ssh(abs_cfg, f"find {paths} -type f -printf '%s\\t%p\\n' 2>/dev/null; true", timeout=120)
    remote = {}
    for line in result.stdout.splitlines():
        size, _, path = line.partition("\t")
        if size.isdigit():
            remote[path] = int(size)
    return [rel for rel, size in files.items() if remote.get(posixpath.join(abs_cfg["remote_dir"], rel)) != size]
