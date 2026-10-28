import threading

_trava = threading.Lock()


def log(etiqueta, mensagem):
    with _trava:
        print(f"[{etiqueta}] {mensagem}", flush=True)


def etiqueta_cap(cap, etapa=""):
    return f"Cap {int(cap['capitulo'])} {etapa}".strip()


def formatar_duracao(segundos):
    segundos = max(0, int(round(segundos)))
    horas, resto = divmod(segundos, 3600)
    minutos, seg = divmod(resto, 60)
    if horas:
        return f"{horas}h{minutos:02d}min"
    if minutos:
        return f"{minutos}min{seg:02d}s"
    return f"{seg}s"


def rotulo_capitulo(cap):
    volume = str(cap.get("volume", "")).strip()
    return f"Vol. {volume} - {cap['titulo']}" if volume else cap["titulo"]
