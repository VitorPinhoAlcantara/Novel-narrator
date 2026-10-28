import json
import os
import queue
import subprocess
import threading
import time

from . import gpu
from ..log import log

RAIZ_PROJETO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class WorkerTTS:
    def __init__(self, indice, comando, log_dir):
        self.indice = indice
        os.makedirs(log_dir, exist_ok=True)
        self._log = open(os.path.join(log_dir, f"worker_{indice}.log"), "w", encoding="utf-8")
        env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        self.proc = subprocess.Popen(
            comando, cwd=RAIZ_PROJETO, env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self._log,
            text=True, encoding="utf-8", bufsize=1,
        )
        self.pronto = None

    def esperar_pronto(self):
        while True:
            linha = self.proc.stdout.readline()
            if not linha:
                raise RuntimeError(f"worker {self.indice} terminou antes de ficar pronto (veja o log)")
            evento = json.loads(linha)
            if evento.get("evento") == "pronto":
                self.pronto = evento
                return evento

    def enviar(self, job):
        try:
            self.proc.stdin.write(json.dumps(job, ensure_ascii=False) + "\n")
            self.proc.stdin.flush()
            linha = self.proc.stdout.readline()
        except (OSError, ValueError):
            return None
        if not linha:
            return None
        return json.loads(linha)

    def encerrar(self):
        try:
            self.proc.stdin.write(json.dumps({"cmd": "sair"}) + "\n")
            self.proc.stdin.flush()
        except (OSError, ValueError):
            pass
        try:
            self.proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self._log.close()


class PoolTTS:
    def __init__(self, config, voz_paths, workers=None, log_dir=None):
        self.config = config
        self.voz_paths = list(voz_paths)
        self.n_workers = max(1, int(workers or config.get("workers", 1)))
        self.log_dir = log_dir or os.path.join("audiobook_work", "logs_tts")
        self.workers = []
        self.asr = None
        self.limite_vivo = self.n_workers

    def _comando_asr(self):
        cfg = self.config
        return [
            cfg["tts_python"], "-u", "-m", "centralnovel.tts.asr_worker",
            "--modelo", cfg["asr_modelo"], "--dispositivo", cfg["dispositivo"],
        ]

    def transcrever(self, itens):
        if self.asr is None:
            self.asr = WorkerTTS("asr", self._comando_asr(), self.log_dir)
            self.asr.esperar_pronto()
        textos = {}
        for id_, wav in itens:
            resposta = self.asr.enviar({"id": id_, "wav": wav})
            textos[id_] = resposta.get("texto", "") if resposta else ""
        return textos

    def _comando(self):
        cfg = self.config
        ref = cfg["xtts_ref"]
        cmd = [
            cfg["tts_python"], "-u", "-m", "centralnovel.tts.engine_worker",
            "--model-dir", cfg["xtts_model_dir"],
            "--idioma", cfg["idioma_xtts"],
            "--dispositivo", cfg["dispositivo"],
            "--latents-cache", os.path.abspath(cfg["latents_cache"]),
            "--gpt-cond-len", str(ref["gpt_cond_len"]),
            "--gpt-cond-chunk-len", str(ref["gpt_cond_chunk_len"]),
            "--max-ref-length", str(ref["max_ref_length"]),
            "--params", json.dumps(self._params_xtts()),
            "--qa", json.dumps(cfg.get("qa", {})),
            "--matmul", cfg.get("matmul", "highest"),
        ]
        for voz in self.voz_paths:
            cmd += ["--voz", os.path.abspath(voz)]
        return cmd

    def _params_xtts(self):
        xtts = self.config["xtts"]
        return {
            "temperature": xtts["temperature"],
            "repetition_penalty": xtts["repetition_penalty"],
            "top_k": xtts["top_k"],
            "top_p": xtts["top_p"],
            "speed": xtts["speed"],
        }

    def iniciar(self):
        comando = self._comando()
        try:
            for i in range(self.n_workers):
                self.workers.append(WorkerTTS(i, comando, self.log_dir))
            for worker in self.workers:
                worker.esperar_pronto()
        except Exception:
            self.encerrar()
            raise
        self.limite_vivo = len(self.workers)
        return [w.pronto for w in self.workers]

    def _podar_mortos(self):
        vivos = []
        for worker in self.workers:
            if worker.proc.poll() is None:
                vivos.append(worker)
            else:
                worker.encerrar()
        self.workers = vivos

    def redimensionar(self, n):
        n = max(1, int(n))
        self._podar_mortos()
        while len(self.workers) > n:
            self.workers.pop().encerrar()
        comando = self._comando()
        novos = []
        usados = {w.indice for w in self.workers}
        try:
            while len(self.workers) + len(novos) < n:
                indice = next(i for i in range(n + len(usados)) if i not in usados)
                usados.add(indice)
                novos.append(WorkerTTS(indice, comando, self.log_dir))
            for worker in novos:
                worker.esperar_pronto()
        except Exception as exc:
            log("XTTS", f"AVISO: nao foi possivel iniciar mais workers: {exc}")
            for worker in novos:
                worker.encerrar()
            novos = []
        self.workers.extend(novos)
        self.n_workers = len(self.workers)
        self.limite_vivo = self.n_workers
        return self.n_workers

    def encerrar(self):
        for worker in self.workers:
            worker.encerrar()
        self.workers = []
        if self.asr is not None:
            self.asr.encerrar()
            self.asr = None

    def __enter__(self):
        self.iniciar()
        return self

    def __exit__(self, *exc):
        self.encerrar()

    def _monitorar_memoria(self, parar):
        critica = self.config.get("vram_critica_mb", 1000)
        folga = self.config.get("vram_folga_mb", 2500)
        while not parar.wait(5):
            try:
                amostra = gpu.amostrar()
            except (OSError, ValueError, IndexError, subprocess.SubprocessError):
                continue
            livre = amostra["total_mb"] - amostra["usada_mb"]
            anterior = self.limite_vivo
            if livre < critica and self.limite_vivo > 1:
                self.limite_vivo -= 1
            elif livre > folga and self.limite_vivo < len(self.workers):
                self.limite_vivo += 1
            if self.limite_vivo != anterior:
                log("GPU", f"VRAM livre {livre} MB: workers de XTTS ativos {anterior} -> {self.limite_vivo}")

    def gerar(self, jobs, ao_concluir=None):
        fila = queue.Queue()
        for job in jobs:
            fila.put(job)
        resultados = {}
        trava = threading.Lock()
        total = len(jobs)
        posicoes = {id(w): i for i, w in enumerate(self.workers)}
        parar = threading.Event()
        if self.config.get("workers_adaptativo", True) and len(self.workers) > 1:
            threading.Thread(target=self._monitorar_memoria, args=(parar,), daemon=True).start()

        def laco(worker):
            posicao = posicoes[id(worker)]
            while True:
                while posicao >= self.limite_vivo and not fila.empty():
                    time.sleep(2)
                try:
                    job = fila.get_nowait()
                except queue.Empty:
                    return
                resposta = worker.enviar(job)
                morreu = resposta is None
                if morreu and job.get("_morte", 0) < 1:
                    job["_morte"] = job.get("_morte", 0) + 1
                    fila.put(job)
                    return
                if morreu:
                    resposta = {"evento": "resultado", "id": job["id"], "ok": False,
                                "tentativas": 0, "metricas": {}, "motivos": ["worker_morreu"],
                                "tempo_s": 0}
                with trava:
                    resultados[job["id"]] = resposta
                    feitos = len(resultados)
                if ao_concluir:
                    ao_concluir(resposta, feitos, total)
                if morreu:
                    return

        threads = [threading.Thread(target=laco, args=(w,), daemon=True) for w in self.workers]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        parar.set()

        while not fila.empty():
            job = fila.get_nowait()
            resultados[job["id"]] = {"evento": "resultado", "id": job["id"], "ok": False,
                                     "tentativas": 0, "metricas": {}, "motivos": ["sem_worker"],
                                     "tempo_s": 0}
        self._podar_mortos()
        return resultados
