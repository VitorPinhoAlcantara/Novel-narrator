import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from novel_narrator.log import chapter_label, chapter_tag, format_duration, log

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
):
    if not chapters:
        print("Nenhum capitulo selecionado")
        return {"successes": 0, "failures": []}
    if "audio" in outputs and audio is None:
        raise ValueError("a saida 'audio' exige uma sessao de audiolivro")
    fetch = fetch or (lambda chapter: (True, None))
    total = len(chapters)
    _print_header(novel_title, total, outputs, overwrite, parallel, lookahead)

    lock = threading.Lock()
    counters = {"ok": 0, "done": 0}
    failures = []

    def record(index, chapter, ok, reason, extra=""):
        with lock:
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
        if on_done:
            on_done(done, total)

    def fetch_and_record(index, chapter):
        record(index, chapter, *_safe(fetch, chapter))

    try:
        if "audio" in outputs:
            _run_with_audio(chapters, fetch, audio, overwrite, lookahead, delay_s, record)
        else:
            with ThreadPoolExecutor(max_workers=parallel) as executor:
                futures = []
                for index, chapter in enumerate(chapters):
                    futures.append(executor.submit(fetch_and_record, index, chapter))
                    time.sleep(delay_s)
                for future in futures:
                    future.result()
    finally:
        if audio is not None:
            audio.close()

    failures.sort(key=lambda item: item[0])
    print(f"\n{'=' * 50}\nSucessos: {counters['ok']}\nFalhas: {len(failures)}\n{'=' * 50}")
    return {"successes": counters["ok"], "failures": [{"chapter": c, "reason": r} for _, c, r in failures]}


def _run_with_audio(chapters, fetch, audio, overwrite, lookahead, delay_s, record):
    ready = queue.Queue()
    slots = threading.Semaphore(lookahead)
    stop = threading.Event()

    def producer():
        for index, chapter in enumerate(chapters):
            slots.acquire()
            if stop.is_set():
                break
            ready.put((index, chapter, *_safe(fetch, chapter)))
            time.sleep(delay_s)
        ready.put(None)

    threading.Thread(target=producer, daemon=True).start()
    durations = []
    try:
        while True:
            item = ready.get()
            if item is None:
                break
            index, chapter, ok, reason = item
            extra = ""
            try:
                if ok and audio.pending(chapter, overwrite):
                    log(
                        chapter_tag(chapter, "audio"),
                        f"comecando o audio (item {index + 1} de {len(chapters)} da lista)",
                    )
                    start = time.time()
                    ok, reason = audio.generate_chapter(chapter)
                    if ok:
                        durations.append(time.time() - start)
                        remaining = sum(1 for c in chapters[index + 1 :] if audio.pending(c, overwrite))
                        if remaining:
                            average = sum(durations[-10:]) / len(durations[-10:])
                            extra = f" | faltam {remaining} audio(s), previsao ~{format_duration(remaining * average)}"
                elif ok:
                    log(chapter_tag(chapter, "audio"), "ja existe no servidor, pulando")
            except Exception as exc:
                ok, reason = False, f"Erro inesperado: {exc}"
            slots.release()
            record(index, chapter, ok, reason, extra)
    finally:
        stop.set()
        slots.release()


def _safe(fetch, chapter):
    try:
        return fetch(chapter)
    except Exception as exc:
        return False, f"Erro inesperado: {exc}"


def _print_header(novel_title, total, outputs, overwrite, parallel, lookahead):
    print("\n" + "=" * 70)
    print(f"{novel_title} | {total} capitulo(s) | saidas: {describe_outputs(outputs)}")
    if outputs == {"audio"}:
        print("Somente audio: nao ha PDF/CBZ para baixar.")
    elif "audio" in outputs:
        print(f"Downloads um por vez, no maximo {lookahead} capitulos a frente do audio.")
    else:
        print(f"Downloads: {parallel} em paralelo.")
    print("Cada linha traz o capitulo e a etapa: [Cap 152 download] = baixando PDF/CBZ,")
    print("[Cap 147 audio] = gerando o audio, [Cap 147 OK] = capitulo concluido, [Envio] = servidor.")
    if overwrite:
        print("Arquivos ja existentes serao sobrescritos.")
    print("=" * 70)
