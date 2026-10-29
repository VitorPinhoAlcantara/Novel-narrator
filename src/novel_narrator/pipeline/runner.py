import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from novel_narrator.log import chapter_label, chapter_tag, format_duration, log
from novel_narrator.pipeline.progress import emit
from novel_narrator.tts.pool import Interrupted

OUTPUT_NAMES = {"pdf": "PDF", "cbz": "CBZ", "audio": "Audiolivro"}


def describe_outputs(outputs):
    return " + ".join(OUTPUT_NAMES[o] for o in ("pdf", "cbz", "audio") if o in outputs)


def run_chapters(
    chapters,
    novel_title,
    outputs,
    fetch=None,
    audio=None,
    overwrite=False,
    parallel=3,
    lookahead=6,
    delay_s=0.3,
    on_done=None,
    stop=None,
    needs_download=None,
):
    if not chapters:
        print("Nenhum capitulo selecionado")
        return {"successes": 0, "failures": [], "stopped": []}
    if "audio" in outputs and audio is None:
        raise ValueError("a saida 'audio' exige uma sessao de audiolivro")
    fetch = fetch or (lambda chapter: (True, None))
    stop = stop or threading.Event()
    total = len(chapters)
    _print_header(novel_title, total, outputs, overwrite, parallel, lookahead)
    has_files = bool(outputs & {"pdf", "cbz"})
    needs_download = needs_download or (lambda chapter: has_files)
    download = {i for i, c in enumerate(chapters) if has_files and needs_download(c)}
    with_audio = {i for i, c in enumerate(chapters) if audio is not None and audio.pending(c, overwrite)}
    skipped = [i for i in range(total) if i not in download and i not in with_audio]
    emit(
        "plan",
        chapters=chapters,
        outputs=outputs,
        download=[chapters[i] for i in sorted(download)],
        audio=[chapters[i] for i in sorted(with_audio)],
        skipped=[chapters[i] for i in skipped],
    )
    log("Plano", _describe_plan(total, len(skipped), download, with_audio, outputs))

    lock = threading.Lock()
    counters = {"ok": 0, "done": 0}
    failures = []
    recorded = set()

    def record(index, chapter, ok, reason, extra=""):
        with lock:
            recorded.add(index)
            counters["done"] += 1
            if ok:
                counters["ok"] += 1
            else:
                failures.append((index, chapter, reason))
            done = counters["done"]
        status = f"{done}/{total} concluidos, restam {total - done} | {chapter_label(chapter)}"
        if ok:
            log(chapter_tag(chapter, "OK"), status + extra)
        else:
            log(chapter_tag(chapter, "ERRO"), f"{status} | motivo: {reason}")
        emit("chapter", chapter=chapter, ok=ok, reason=reason)
        if on_done:
            on_done(done, total)

    def fetch_one(index, chapter):
        if index not in download:
            return True, None
        emit("download", chapter=chapter, state="run")
        ok, reason = _safe(fetch, chapter)
        emit("download", chapter=chapter, state="ok" if ok else "bad", reason=reason or "")
        return ok, reason

    def fetch_and_record(index, chapter):
        if not stop.is_set():
            record(index, chapter, *fetch_one(index, chapter))

    with lock:
        recorded.update(skipped)
        counters["done"] = counters["ok"] = len(skipped)
    if skipped and on_done:
        on_done(len(skipped), total)
    todo = [(i, c) for i, c in enumerate(chapters) if i not in recorded]

    try:
        if "audio" in outputs:
            _run_with_audio(todo, len(chapters), fetch_one, audio, overwrite, lookahead, delay_s, record, stop)
        else:
            with ThreadPoolExecutor(max_workers=parallel) as executor:
                futures = []
                for index, chapter in todo:
                    if stop.is_set():
                        break
                    futures.append(executor.submit(fetch_and_record, index, chapter))
                    time.sleep(delay_s)
                for future in futures:
                    future.result()
    finally:
        emit("finish")
        if audio is not None:
            audio.close()

    failures.sort(key=lambda item: item[0])
    stopped = [c for i, c in enumerate(chapters) if i not in recorded]
    summary = f"Sucessos: {counters['ok']}\nFalhas: {len(failures)}"
    if stopped:
        summary += f"\nInterrompidos: {len(stopped)}"
    print(f"\n{'=' * 50}\n{summary}\n{'=' * 50}")
    return {
        "successes": counters["ok"],
        "failures": [{"chapter": c, "reason": r} for _, c, r in failures],
        "stopped": stopped,
    }


def _run_with_audio(todo, total, fetch_one, audio, overwrite, lookahead, delay_s, record, stop):
    ready = queue.Queue()
    slots = threading.Semaphore(lookahead)
    done = threading.Event()

    def producer():
        for index, chapter in todo:
            slots.acquire()
            if done.is_set() or stop.is_set():
                break
            ready.put((index, chapter, *fetch_one(index, chapter)))
            time.sleep(delay_s)
        ready.put(None)

    threading.Thread(target=producer, daemon=True).start()
    durations = []
    try:
        while True:
            item = ready.get()
            if item is None or stop.is_set():
                break
            index, chapter, ok, reason = item
            extra = ""
            try:
                if ok and audio.pending(chapter, overwrite):
                    log(
                        chapter_tag(chapter, "audio"),
                        f"comecando o audio (item {index + 1} de {total} da lista)",
                    )
                    emit("audio_start", chapter=chapter)
                    start = time.time()
                    ok, reason = audio.generate_chapter(chapter)
                    if ok:
                        durations.append(time.time() - start)
                        remaining = sum(1 for i, c in todo if i > index and audio.pending(c, overwrite))
                        if remaining:
                            average = sum(durations[-10:]) / len(durations[-10:])
                            extra = f" | faltam {remaining} audio(s), previsao ~{format_duration(remaining * average)}"
                elif ok:
                    log(chapter_tag(chapter, "audio"), "ja existe no servidor, pulando")
            except Interrupted:
                break
            except Exception as exc:
                ok, reason = False, f"Erro inesperado: {exc}"
            slots.release()
            record(index, chapter, ok, reason, extra)
    finally:
        done.set()
        slots.release()


def _safe(fetch, chapter):
    try:
        return fetch(chapter)
    except Exception as exc:
        return False, f"Erro inesperado: {exc}"


def _describe_plan(total, skipped, download, with_audio, outputs):
    parts = [f"{total} capitulo(s)"]
    if skipped:
        parts.append(f"{skipped} ja completo(s), pulado(s)")
    if outputs & {"pdf", "cbz"}:
        parts.append(f"{len(download)} para baixar")
    if "audio" in outputs:
        parts.append(f"{len(with_audio)} para gerar audio")
    return " | ".join(parts)


def _print_header(novel_title, total, outputs, overwrite, parallel, lookahead):
    print("\n" + "=" * 70)
    print(f"{novel_title} | {total} capitulo(s) | saidas: {describe_outputs(outputs)}")
    if outputs == {"audio"}:
        print("Somente audio: nao ha PDF/CBZ para baixar.")
    elif "audio" in outputs:
        print(f"Downloads um por vez, no maximo {lookahead} capitulos a frente do audio.")
    else:
        print(f"Downloads: {parallel} em paralelo.")
    if overwrite:
        print("Arquivos ja existentes serao sobrescritos.")
    print("=" * 70)
