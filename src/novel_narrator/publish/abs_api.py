import posixpath
import subprocess
import time

import requests

from novel_narrator.log import log
from novel_narrator.publish.abs_server import ssh

_MOUNTS_COMMAND = (
    "for n in $(docker ps --format '{{.Names}} {{.Image}}' | awk '/audiobookshelf/ {print $1}'); do "
    "docker inspect \"$n\" --format '{{range .Mounts}}{{.Source}}|{{.Destination}}{{println}}{{end}}'; done"
)


def list_libraries(abs_cfg, url=None, token=None, timeout=15):
    url = (url or abs_cfg["url"]).rstrip("/")
    token = token if token is not None else abs_cfg.get("token", "")
    response = requests.get(f"{url}/api/libraries", headers={"Authorization": f"Bearer {token}"}, timeout=timeout)
    response.raise_for_status()
    return [
        {
            "id": lib["id"],
            "name": lib["name"],
            "type": lib.get("mediaType", ""),
            "folders": [f.get("fullPath") for f in lib.get("folders", []) if f.get("fullPath")],
        }
        for lib in response.json().get("libraries", [])
    ]


def _headers(abs_cfg):
    return {"Authorization": f"Bearer {abs_cfg['token']}"}


def _api(abs_cfg, method, path, **kwargs):
    response = requests.request(
        method, f"{abs_cfg['url'].rstrip('/')}/api/{path}", headers=_headers(abs_cfg), timeout=30, **kwargs
    )
    response.raise_for_status()
    return response.json() if response.content else {}


def scan_library(abs_cfg):
    if not abs_cfg.get("token"):
        log("Envio", "AVISO: sem token do Audiobookshelf (ABS_TOKEN); faca o scan da biblioteca manualmente")
        return None
    try:
        libraries = list_libraries(abs_cfg)
        name = abs_cfg.get("library", "")
        chosen = next((lib for lib in libraries if lib["name"] == name), None) if name else None
        if chosen is None and len(libraries) == 1:
            chosen = libraries[0]
        if chosen is None:
            available = ", ".join(lib["name"] for lib in libraries)
            log("Envio", f"AVISO: defina [abs] library no config.toml (disponiveis: {available})")
            return None
        _api(abs_cfg, "POST", f"libraries/{chosen['id']}/scan")
    except requests.RequestException as exc:
        log("Envio", f"AVISO: nao foi possivel acionar o scan do Audiobookshelf: {exc}")
        return None
    log("Envio", f"scan do Audiobookshelf solicitado (biblioteca {chosen['name']})")
    return chosen


def chapters_from_tracks(tracks):
    chapters = []
    for index, track in enumerate(sorted(tracks, key=lambda t: t.get("index", 0))):
        start = float(track.get("startOffset", 0))
        filename = (track.get("metadata") or {}).get("filename") or track.get("title", "")
        title = posixpath.splitext(filename)[0] or f"Capitulo {index + 1}"
        chapters.append({"id": index, "start": start, "end": start + float(track.get("duration", 0)), "title": title})
    return chapters


def _same_chapters(current, wanted):
    if len(current) != len(wanted):
        return False
    return all(
        c.get("title") == w["title"] and abs(float(c.get("start", 0)) - w["start"]) < 0.5
        for c, w in zip(current, wanted, strict=True)
    )


def sync_chapters(abs_cfg, library, folders, wait_s=300, poll_s=5, sleep=time.sleep, clock=time.monotonic):
    pending = dict(folders)
    deadline = clock() + wait_s
    updated = 0
    try:
        while pending:
            items = _api(abs_cfg, "GET", f"libraries/{library['id']}/items").get("results", [])
            by_path = {item.get("relPath", "").strip("/"): item["id"] for item in items}
            for rel_path, expected in list(pending.items()):
                item_id = by_path.get(rel_path)
                if item_id is None:
                    continue
                media = _api(abs_cfg, "GET", f"items/{item_id}", params={"expanded": 1})["media"]
                tracks = media.get("tracks", [])
                if len(tracks) < expected:
                    continue
                del pending[rel_path]
                wanted = chapters_from_tracks(tracks)
                if not _same_chapters(media.get("chapters", []), wanted):
                    _api(abs_cfg, "POST", f"items/{item_id}/chapters", json={"chapters": wanted})
                    updated += 1
                    log("Envio", f"capitulos atualizados no Audiobookshelf: {rel_path} ({len(wanted)} capitulos)")
            if pending and clock() >= deadline:
                log(
                    "Envio",
                    f"AVISO: o scan do Audiobookshelf ainda nao terminou para {', '.join(pending)}; "
                    "os capitulos serao atualizados no proximo envio",
                )
                break
            if pending:
                sleep(poll_s)
    except (requests.RequestException, KeyError, ValueError) as exc:
        log("Envio", f"AVISO: nao foi possivel atualizar os capitulos no Audiobookshelf: {exc}")
    return updated


def detect_mounts(abs_cfg):
    try:
        result = ssh(abs_cfg, _MOUNTS_COMMAND, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return {}
    mounts = {}
    for line in result.stdout.splitlines():
        if "|" in line:
            source, destination = line.split("|", 1)
            if source.strip() and destination.strip():
                mounts[destination.strip()] = source.strip()
    return mounts


def server_folder(container_path, mounts):
    best = None
    for destination in mounts:
        root = destination.rstrip("/")
        if container_path == root or container_path.startswith(root + "/"):
            if best is None or len(root) > len(best.rstrip("/")):
                best = destination
    if best is None:
        return None
    rest = container_path[len(best.rstrip("/")) :].lstrip("/")
    return posixpath.join(mounts[best], rest) if rest else mounts[best]


def libraries_sharing_folder(libraries, target, folder):
    root = folder.rstrip("/")
    others = []
    for lib in libraries:
        if lib["id"] == target["id"]:
            continue
        for other in lib["folders"]:
            other = other.rstrip("/")
            if other == root or other.startswith(root + "/") or root.startswith(other + "/"):
                others.append(lib["name"])
                break
    return others
