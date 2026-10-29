import json
import os
import re
import shutil
import threading
import time

import soundfile as sf

from novel_narrator.log import chapter_label, chapter_tag, format_duration, log, summarize_chapters
from novel_narrator.models import Chapter
from novel_narrator.outputs.epub_writer import write_volume_epub
from novel_narrator.pipeline.progress import emit
from novel_narrator.pipeline.state import classify_segments, load_json, segment_seed, write_json_atomic
from novel_narrator.publish import abs_server
from novel_narrator.publish.abs_api import scan_library, sync_chapters
from novel_narrator.sources.base import SourceError
from novel_narrator.text.emotion import classify
from novel_narrator.text.normalize import adjust_ending, normalize
from novel_narrator.text.split import split_paragraphs
from novel_narrator.tts.assemble import (
    assemble_chapter,
    crop,
    cut_at,
    detect_final_burst,
    encode_m4b,
    file_duration,
)
from novel_narrator.tts.gpu import choose_workers
from novel_narrator.tts.pool import Interrupted, TTSPool
from novel_narrator.tts.verify import analyze, has_similar_word, words


def safe_name(text):
    text = re.sub(r'[<>:"/\\|?*]', "", str(text))
    return " ".join(text.split()).rstrip(". ")


def volume_title(config, novel_title, volume):
    return config["audiobook"]["volume_title"].format(novel=novel_title, volume=volume)


def relative_path(config, author, novel_title, chapter, book_mode=False):
    extension = config["audiobook"]["format"]
    if book_mode:
        name = f"{chapter.number:03d} - {chapter.title}.{extension}"
        return (safe_name(author), safe_name(novel_title), safe_name(name))
    volume = str(chapter.volume).strip()
    title = volume_title(config, novel_title, volume)
    folder = f"Vol. {volume} - {title}" if volume.isdigit() else title
    name = f"{chapter.number:04d} - Capitulo {chapter.number} - {chapter.title}.{extension}"
    return (safe_name(author), safe_name(novel_title), safe_name(folder), safe_name(name))


def volume_epub_path(config, author, novel_title, volume):
    folder = relative_path(config, author, novel_title, _volume_placeholder(volume))[:-1]
    return folder + (safe_name(f"{volume_title(config, novel_title, volume)}.epub"),)


def chapter_metadata(config, author, novel_title, chapter, spoken_title, book_mode=False):
    meta = {
        "title": chapter.title if book_mode else spoken_title,
        "artist": author,
        "album_artist": author,
        "album": novel_title if book_mode else volume_title(config, novel_title, chapter.volume),
        "track": str(chapter.number),
        "genre": "Audiobook",
        "language": config["audiobook"]["language"],
    }
    if not book_mode:
        meta["series"] = novel_title
        if str(chapter.volume).isdigit():
            meta["series-part"] = str(chapter.volume)
    return meta


class AudiobookSession:
    def __init__(self, source, novel, config, voice=None, book_mode=False, seed_salt=None):
        self.source = source
        self.seed_salt = seed_salt
        self.novel = novel
        self.config = config
        self.voice = voice or config["tts"]["voice"]
        self.book_mode = book_mode
        self.data_dir = config["paths"]["data_dir"]
        self.author = novel.author or config["audiobook"]["default_author"]
        self.existing = set()
        self.pool = None
        self.aborted = False
        self.published = []
        self.covered_folders = set()
        self._cover = None
        self._epub_attached = False
        self._prepared = False
        self._prepare_error = None
        self._tag = "audio"
        self._syncs = []

    def pending(self, chapter, overwrite=False):
        self.prepare()
        if self._prepare_error or overwrite:
            return True
        return self._relative(chapter) not in self.existing

    def generate_chapter(self, chapter):
        if self.aborted:
            raise Interrupted()
        self.prepare()
        if self._prepare_error:
            return False, self._prepare_error
        try:
            paragraphs = list(self.source.chapter_text(chapter))
        except SourceError as exc:
            return False, f"texto indisponivel: {exc}"

        self._tag = chapter_tag(chapter, "audio")
        start = time.time()
        spoken_title = "" if self.book_mode else f"Capítulo {chapter.number}: {chapter.title}"
        self._ensure_cover(chapter)
        if not self.book_mode:
            self._store_text(chapter, spoken_title, paragraphs)

        options = self.config["text"]["normalization"]
        texts = [normalize(p, options) for p in ([spoken_title] if spoken_title else []) + paragraphs]
        pauses = self.config["text"]["pauses_ms"]
        segments = split_paragraphs(
            [t for t in texts if t], sentence_pause_ms=pauses["sentence"], paragraph_pause_ms=pauses["paragraph"]
        )
        if not segments:
            return False, "capitulo sem texto apos a normalizacao"

        folder = os.path.join(
            self.data_dir, "work", safe_name(self.novel.title), f"cap_{chapter.volume}_{chapter.number:04d}"
        )
        self._log(f"{chapter_label(chapter)} | texto dividido em {len(segments)} trechos")
        try:
            results = self._synthesize(chapter, segments, folder)
        except (KeyboardInterrupt, Interrupted):
            saved = len([n for n in os.listdir(folder) if n.endswith(".wav")]) if os.path.isdir(folder) else 0
            self._log(
                f"INTERROMPIDO: {saved} de {len(segments)} trechos guardados em {folder}. "
                "Este capitulo NAO sera enviado ao servidor e continua de onde parou na proxima vez"
            )
            raise
        except RuntimeError as exc:
            return False, str(exc)

        missing = [s["id"] for s in segments if not os.path.exists(os.path.join(folder, f"{s['id']}.wav"))]
        if missing:
            return False, f"{len(missing)} trecho(s) sem audio (ex.: {', '.join(missing[:5])})"

        qa_path = self._write_qa_report(chapter, segments, results)
        self._log("montando o audio e codificando o m4b...")
        emit("stage", text="montando o áudio e codificando o m4b")
        wav = os.path.join(folder, "chapter.wav")
        destination = os.path.join(abs_server.publish_root(self.config), *self._relative(chapter))
        metadata = chapter_metadata(self.config, self.author, self.novel.title, chapter, spoken_title, self.book_mode)
        try:
            duration = assemble_chapter(segments, folder, wav)
            encode_m4b(wav, destination, metadata, bitrate=self.config["audiobook"]["bitrate"])
        except (OSError, RuntimeError) as exc:
            return False, f"falha ao montar o audio: {exc}"

        asr_alerts = sum(1 for r in results.values() if r.get("asr_reasons"))
        qa_alerts = sum(1 for r in results.values() if not r.get("ok") and not r.get("asr_reasons"))
        report = f" | relatorio: {qa_path}" if (asr_alerts or qa_alerts) else ""
        emit(
            "audio_done",
            chapter=chapter,
            minutes=duration / 60,
            seconds=time.time() - start,
            alerts=asr_alerts + qa_alerts,
            report=qa_path if (asr_alerts or qa_alerts) else "",
        )
        self._log(
            f"pronto em {format_duration(time.time() - start)}: {duration / 60:.1f} min de audio, "
            f"{len(segments)} trechos | alertas: Whisper {asr_alerts}, QA numerico {qa_alerts}{report}"
        )
        shutil.rmtree(folder, ignore_errors=True)
        self.published.append(chapter)
        self.existing.add(self._relative(chapter))
        if len(self.published) >= max(1, int(self.config["audiobook"]["batch_chapters"])):
            self._publish_batch()
        return True, None

    def abort(self):
        self.aborted = True
        if self.pool is not None:
            self.pool.abort()

    def close(self):
        try:
            self._publish_batch()
            for thread in self._syncs:
                thread.join()
        finally:
            if self.pool is not None:
                self.pool.close()
                self.pool = None

    def _relative(self, chapter):
        return relative_path(self.config, self.author, self.novel.title, chapter, self.book_mode)

    def _log(self, message):
        log(self._tag, message)

    def prepare(self):
        if self._prepared:
            return self._prepare_error
        self._prepared = True
        error = self._validate_environment()
        if error:
            self._prepare_error = error
            log("Preparo", f"ERRO: {error}")
            return error
        log("Preparo", f"autor: {self.author}")
        try:
            self._discard_incomplete_outbox()
            self._warn_orphan_outbox()
            resent = abs_server.send_outbox(self.config)
            if resent:
                log("Preparo", f"{resent} arquivo(s) de execucoes anteriores que aguardavam envio foram enviados")
            base = (safe_name(self.author), safe_name(self.novel.title))
            self.existing = abs_server.list_existing(self.config, base)
        except RuntimeError as exc:
            self._prepare_error = f"servidor do Audiobookshelf indisponivel: {exc}"
            log("Preparo", f"ERRO: {self._prepare_error}")
        return self._prepare_error

    def _validate_environment(self):
        paths = self.config["paths"]
        if paths["tts_python"] and not os.path.isfile(paths["tts_python"]):
            return f"Python do TTS nao encontrado: {paths['tts_python']}"
        if not os.path.isfile(os.path.join(paths["model_dir"], "model.pth")):
            return f"Modelo XTTS nao encontrado em: {paths['model_dir']}"
        if not os.path.isfile(self._voice_path()):
            return f"Voz nao encontrada: {self._voice_path()}"
        if shutil.which("ffmpeg") is None:
            return "ffmpeg nao encontrado no PATH"
        return abs_server.check_destination(self.config["abs"])

    def _voice_path(self):
        return os.path.join(self.config["paths"]["voices_dir"], self.voice)

    def _discard_incomplete_outbox(self):
        outbox = abs_server.outbox_dir(self.config)
        if not os.path.isdir(outbox):
            return
        discarded = os.path.join(self.data_dir, "discarded")
        extension = f".{self.config['audiobook']['format']}"
        for root, _, names in os.walk(outbox):
            for name in names:
                path = os.path.join(root, name)
                if name.endswith(".part"):
                    os.remove(path)
                    log("Preparo", f"removido arquivo temporario incompleto: {name}")
                elif name.endswith(extension) and file_duration(path) < 30:
                    os.makedirs(discarded, exist_ok=True)
                    os.replace(path, os.path.join(discarded, name))
                    log("Preparo", f"audio incompleto na fila de envio movido para {discarded}: {name}")

    def _warn_orphan_outbox(self):
        root = abs_server.outbox_root(self.config)
        if not os.path.isdir(root) or abs_server.outbox_dir(self.config) == root:
            return
        orphans = [n for n in os.listdir(root) if not re.fullmatch(r".+_[0-9a-f]{10}", n)]
        if orphans:
            log(
                "Preparo",
                f"AVISO: ha arquivos antigos na fila de envio sem destino definido "
                f"({', '.join(orphans)} em {root}); eles NAO foram enviados",
            )

    def _start_pool(self):
        if self.pool is not None:
            n, reading = choose_workers(self.config["tts"], len(self.pool.workers))
            if n != len(self.pool.workers):
                self._announce_workers(n, reading)
                self.pool.resize(n)
            return
        n, reading = choose_workers(self.config["tts"], 0)
        self._announce_workers(n, reading)
        pool = TTSPool(self.config, [self._voice_path()], workers=n)
        log("XTTS", f"carregando o modelo em {pool.n_workers} worker(s) (cerca de 1 minuto)...")
        emit("stage", text="carregando o modelo XTTS")
        self.pool = pool
        try:
            pool.start()
        except Exception as exc:
            self.pool = None
            if self.aborted:
                raise Interrupted() from exc
            raise RuntimeError(f"falha ao iniciar o XTTS: {exc}") from exc
        log("XTTS", "modelo carregado e pronto")

    def _announce_workers(self, n, reading):
        if reading:
            log(
                "GPU",
                f"uso {reading['util']}%, VRAM {reading['used_mb']}/{reading['total_mb']} MB -> {n} worker(s) de XTTS",
            )

    def _synthesize(self, chapter, segments, folder):
        os.makedirs(folder, exist_ok=True)
        state_path = os.path.join(folder, "state.json")
        state = load_json(state_path)
        uses_asr = bool(self.config["tts"]["asr"]["enabled"])
        results, to_verify, to_generate = classify_segments(segments, state, folder, uses_asr)
        # A redo needs fresh seeds, otherwise XTTS repeats the same flawed takes.
        salt = () if self.seed_salt is None else (self.seed_salt,)
        seeds = {
            s["id"]: segment_seed(self.novel.title, chapter.volume, chapter.number, s["id"], *salt) for s in segments
        }
        jobs = [self._job(s, folder, seeds[s["id"]], 0) for s in to_generate]
        emit("segments", ids=[s["id"] for s in segments], done=list(results))

        if results:
            suffix = f" ({len(to_verify)} deles so faltam a verificacao do Whisper)" if to_verify else ""
            self._log(f"retomando: {len(results)} de {len(segments)} trechos ja estavam gerados{suffix}")
        if not jobs and not to_verify:
            return results

        self._start_pool()
        texts = {s["id"]: s["text"] for s in segments}
        lock = threading.Lock()
        progress = {"quarter": 0}

        def save_state():
            write_json_atomic(state_path, state)

        def on_done(response, done, total):
            with lock:
                state[response["id"]] = {"text": texts[response["id"]], "result": response}
                save_state()
                quarter = (done * 4) // total
                advanced = quarter > progress["quarter"]
                if advanced:
                    progress["quarter"] = quarter
            if advanced:
                self._log(f"gerando trechos: {quarter * 25}% ({done}/{total})")
            _segment_done(response)

        def on_start(job, worker):
            emit("segment_start", id=job["id"], text=texts[job["id"]], worker=worker)

        if jobs:
            results.update(self.pool.generate(jobs, on_done=on_done, on_start=on_start))
        if uses_asr:
            ids = [j["id"] for j in jobs] + to_verify
            self._verify_with_asr(ids, texts, folder, seeds, results, state, save_state, on_start)
        return results

    def _job(self, segment, folder, seed, round_number):
        endings = [self.config["text"]["segment_ending"], "none", self.config["text"]["segment_ending"]]
        job = {
            "id": segment["id"],
            "text": adjust_ending(segment["text"], endings[round_number % len(endings)]),
            "output": os.path.abspath(os.path.join(folder, f"{segment['id']}.wav")),
            "seed": seed + round_number * 104729,
            "attempts": int(self.config["tts"]["qa_attempts"]),
            "emotion": classify(segment["text"]),
        }
        retry_params = self.config["tts"]["asr"]["retry_params"].get(str(round_number))
        if retry_params:
            job["params"] = retry_params
        return job

    def _verify_with_asr(self, ids, texts, folder, seeds, results, state, save_state, on_start=None):
        pending = list(ids)
        rounds = int(self.config["tts"]["asr"]["rounds"])
        analyses = {}
        trimmed = 0
        self._log(f"verificando {len(pending)} trechos com o Whisper...")
        for round_number in range(rounds + 1):
            emit("stage", text=f"conferindo {len(pending)} trecho(s) com o Whisper")
            transcripts = self.pool.transcribe([(i, os.path.join(folder, f"{i}.wav")) for i in pending])
            bad = []
            for i in pending:
                analysis = analyze(texts[i], transcripts[i], self.config["tts"]["asr"]["limits"])
                analyses[i] = analysis
                results[i]["asr_reasons"] = analysis["reasons"]
                state[i]["result"] = results[i]
                state[i]["verified"] = True
                if not analysis["ok"]:
                    bad.append(i)
                emit("segment_done", id=i, ok=analysis["ok"])
            save_state()
            if self.config["tts"]["trim_final_burst"]:
                trimmed += self._trim_final_bursts(pending, texts, folder)
            if not bad or round_number == rounds:
                break
            self._log(f"correcao {round_number + 1} de {rounds}: refazendo {len(bad)} trecho(s) que o Whisper reprovou")
            jobs = [self._job({"id": i, "text": texts[i]}, folder, seeds[i], round_number + 1) for i in bad]
            for i, result in self.pool.generate(jobs, on_done=_segment_done, on_start=on_start).items():
                results[i] = result
                state[i]["result"] = result
                state[i]["verified"] = False
            pending = bad
        if trimmed:
            self._log(f"ruido apos o fim da frase removido em {trimmed} trecho(s)")
        remaining = [i for i, a in analyses.items() if not a["ok"]]
        if remaining:
            self._log(
                f"{len(remaining)} trecho(s) continuam com alerta apos {rounds} correcao(oes) (detalhes no relatorio)"
            )

    def _trim_final_bursts(self, ids, texts, folder):
        candidates = {}
        for i in ids:
            path = os.path.join(folder, f"{i}.wav")
            wav, rate = sf.read(path, dtype="float32")
            burst = detect_final_burst(wav, rate)
            if burst:
                candidates[i] = (path, wav, rate, burst)
        if not candidates:
            return 0
        clips = []
        for i, (_, wav, rate, burst) in candidates.items():
            clip = os.path.join(folder, f"{i}_tail.wav")
            sf.write(clip, crop(wav, burst["start"], burst["end"], rate), rate, subtype="PCM_16")
            clips.append((i, clip))
        transcripts = self.pool.transcribe(clips)
        trimmed = 0
        for i, (path, wav, rate, burst) in candidates.items():
            expected = words(texts[i])
            if expected and has_similar_word(transcripts[i], expected[-1]):
                continue
            sf.write(path, cut_at(wav, burst["cut"], rate), rate, subtype="PCM_16")
            trimmed += 1
        for _, clip in clips:
            os.remove(clip)
        return trimmed

    def _write_qa_report(self, chapter, segments, results):
        folder = os.path.join(self.data_dir, "qa", safe_name(self.novel.title))
        os.makedirs(folder, exist_ok=True)
        texts = {s["id"]: s["text"] for s in segments}
        alerts = [
            {
                "id": i,
                "text": texts.get(i, ""),
                "reasons": r.get("reasons"),
                "asr_reasons": r.get("asr_reasons"),
                "attempts": r.get("attempts"),
                "metrics": r.get("metrics"),
            }
            for i, r in sorted(results.items())
            if not r.get("ok") or r.get("asr_reasons")
        ]
        volume = str(chapter.volume).strip()
        name = f"vol{volume}_cap{chapter.number:04d}.json" if volume else f"cap{chapter.number:04d}.json"
        path = os.path.join(folder, name)
        with open(path, "w", encoding="utf-8") as file:
            json.dump({"segments": len(segments), "alerts": alerts}, file, ensure_ascii=False, indent=1)
        return path

    def _ensure_cover(self, chapter):
        folder = self._relative(chapter)[:-1]
        if folder in self.covered_folders:
            return
        self.covered_folders.add(folder)
        if any(folder + (f"cover.{ext}",) in self.existing for ext in ("jpg", "png")):
            return
        if self._cover is None:
            try:
                self._cover = self.source.cover(self.novel) or False
            except SourceError as exc:
                log("Preparo", f"AVISO: nao foi possivel baixar a capa: {exc}")
                self._cover = False
        if not self._cover:
            return
        data, extension = self._cover
        path = os.path.join(abs_server.publish_root(self.config), *folder, f"cover.{extension}")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as file:
            file.write(data)

    def _attach_original_epub(self, chapter):
        origin = self.novel.url
        if self._epub_attached or not origin.lower().endswith(".epub") or not os.path.isfile(origin):
            return
        self._epub_attached = True
        parts = self._relative(chapter)[:-1] + (safe_name(f"{self.novel.title}.epub"),)
        if parts in self.existing:
            return
        destination = os.path.join(abs_server.publish_root(self.config), *parts)
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        shutil.copyfile(origin, destination)

    def _publish_batch(self):
        if not self.published:
            return
        published, self.published = self.published, []
        log(
            "Envio",
            f"enviando {len(published)} capitulo(s) concluido(s) ({summarize_chapters(published)}) ao servidor...",
        )
        if self.book_mode:
            self._attach_original_epub(published[0])
        else:
            for volume in sorted({str(c.volume).strip() for c in published}):
                self._write_volume_epub(volume)
        if abs_server.is_remote(self.config["abs"]):
            try:
                sent = abs_server.send_outbox(self.config)
            except RuntimeError as exc:
                log(
                    "Envio",
                    f"FALHA ao enviar ({exc}); os audios continuam na fila de envio e serao "
                    "reenviados na proxima execucao",
                )
                return
            log("Envio", f"OK: {sent} arquivo(s) enviados a {self.config['abs']['ssh']} (audios, capa e EPUB)")
        else:
            log("Envio", f"OK: {len(published)} capitulo(s) gravados em {self.config['abs']['local_dir']}")
        library = scan_library(self.config["abs"])
        if library:
            folders = {}
            for chapter in published:
                folder = self._relative(chapter)[:-1]
                folders["/".join(folder)] = self._track_count(folder)
            thread = threading.Thread(target=sync_chapters, args=(self.config["abs"], library, folders), daemon=True)
            thread.start()
            self._syncs.append(thread)

    def _track_count(self, folder):
        extension = f".{self.config['audiobook']['format']}"
        return sum(1 for parts in self.existing if parts[:-1] == folder and parts[-1].endswith(extension))

    def _texts_folder(self, volume):
        return os.path.join(self.data_dir, "texts", safe_name(self.novel.title), f"vol_{volume}")

    def _store_text(self, chapter, spoken_title, paragraphs):
        folder = self._texts_folder(chapter.volume)
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, f"{chapter.number:04d}.json"), "w", encoding="utf-8") as file:
            json.dump({"title": spoken_title, "paragraphs": paragraphs}, file, ensure_ascii=False)

    def _write_volume_epub(self, volume):
        folder = self._texts_folder(volume)
        if not os.path.isdir(folder):
            return
        chapters = []
        for name in sorted(os.listdir(folder)):
            with open(os.path.join(folder, name), encoding="utf-8") as file:
                data = json.load(file)
            chapters.append((data["title"], data["paragraphs"]))
        if not chapters:
            return
        parts = volume_epub_path(self.config, self.author, self.novel.title, volume)
        destination = os.path.join(abs_server.publish_root(self.config), *parts)
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        write_volume_epub(
            destination,
            volume_title(self.config, self.novel.title, volume),
            chapters,
            author=self.author,
            series=self.novel.title,
            series_index=volume if volume.isdigit() else None,
            language="pt",
        )


def _segment_done(response, *_):
    emit("segment_done", id=response["id"], ok=bool(response.get("ok")))


def _volume_placeholder(volume):
    return Chapter(number=1, title="x", volume=volume)
