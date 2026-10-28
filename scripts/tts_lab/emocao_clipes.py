import argparse
import json
import os

import librosa
import numpy as np
import soundfile as sf

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PREP = os.path.join(RAIZ, "audiobook_work", "finetune", "prep")


def medir(caminho):
    wav, taxa = sf.read(caminho, dtype="float32")
    if wav.ndim > 1:
        wav = wav.mean(axis=1)
    rms = librosa.feature.rms(y=wav)[0]
    voz = rms > rms.max() * 0.1
    f0 = librosa.yin(wav, fmin=60, fmax=400, sr=taxa)
    f0 = f0[: len(voz)][voz[: len(f0)]]
    semitons = 12 * np.log2(f0 / 100)
    return {
        "db": float(20 * np.log10(np.mean(rms[voz]) + 1e-9)),
        "tom": float(np.median(semitons)),
        "variacao": float(np.std(semitons)),
    }


def z(valores):
    v = np.asarray(valores)
    return (v - v.mean()) / (v.std() + 1e-9)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--saida", default=os.path.join(RAIZ, "audiobook_work", "emocao_lab", "clipes.json"))
    p.add_argument("--top", type=int, default=12)
    args = p.parse_args()

    pasta = os.path.join(PREP, "dataset_v2")
    with open(os.path.join(pasta, "metadata.csv"), encoding="utf-8") as f:
        linhas = [l.rstrip("\n").split("|") for l in f][1:]
    clipes = []
    for arquivo, texto, _ in linhas:
        dur = sf.info(os.path.join(pasta, arquivo)).duration
        if 5 <= dur <= 12:
            clipes.append({"arquivo": os.path.join(pasta, arquivo), "texto": texto, "dur": dur, "cps": len(texto) / dur})

    for i, c in enumerate(clipes):
        c.update(medir(c["arquivo"]))
        if i % 500 == 0:
            print(i, len(clipes), flush=True)

    db, tom, var, cps = (z([c[k] for c in clipes]) for k in ("db", "tom", "variacao", "cps"))
    for i, c in enumerate(clipes):
        t = c["texto"]
        c["energico"] = db[i] + tom[i] + var[i] + (1.5 if "!" in t else 0)
        c["pergunta"] = var[i] + tom[i] + (3 if "?" in t else -3)
        c["suave"] = -db[i] - var[i] - cps[i] - tom[i] - (2 if "!" in t else 0)

    ranking = {}
    for cat in ("energico", "pergunta", "suave"):
        ranking[cat] = sorted(clipes, key=lambda c: -c[cat])[: args.top]
        print(f"\n== {cat}")
        for c in ranking[cat]:
            print(f"{c[cat]:5.1f} db={c['db']:.1f} tom={c['tom']:.1f} var={c['variacao']:.2f} cps={c['cps']:.1f} {c['arquivo']} | {c['texto'][:110]}")

    os.makedirs(os.path.dirname(args.saida), exist_ok=True)
    with open(args.saida, "w", encoding="utf-8") as f:
        json.dump(ranking, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
