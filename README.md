# Novel Narrator

Download web novels, keep them as CBZ/PDF, and turn them into audiobooks narrated by a fine-tuned XTTS voice, published straight to [Audiobookshelf](https://www.audiobookshelf.org/).

The program's menus and web interface are in Portuguese. *Resumo em português no final.*

## Features

- **Sources:** CentralNovel (search, popular list, or a pasted link) and local EPUB files.
- **Outputs:** CBZ and audiobook on by default, PDF optional. Before each run, a plan shows what already exists and what is missing, per output, so finished chapters are skipped and a chapter only produces the output it lacks.
- **Audiobooks:**
  - XTTS v2 runs in worker processes. The number of workers adapts to the GPU's load and free VRAM.
  - Text normalization (numbers, abbreviations, punctuation) and sentence/paragraph pauses.
  - Numeric QA, plus Whisper verification that regenerates segments the transcript rejects.
  - One `.m4b` per chapter with chapter metadata, the cover, and an EPUB for each volume.
  - Optional emotion per segment, off by default.
- **Pause and resume:** progress is saved per segment. Stopping (Ctrl+C in the terminal, **Pausar** on the web) frees the GPU immediately, and the next run continues from the segment where it stopped.
- **Live progress:**
  - Terminal: a fixed bottom panel with progress bars and the sentence each worker is converting right now, plus one line per finished chapter.
  - Web: two views. **Agora** shows the chapter being narrated and the live sentences; **Mapa** shows one tile per chapter, grouped by volume.
- **Publishing:** to a local Audiobookshelf folder, or to a server over SSH. A library scan is triggered through the API. Files that fail to send wait in an outbox and are retried on the next run.

## Requirements

- Python 3.12 and [uv](https://docs.astral.sh/uv/)
- ffmpeg on `PATH`
- For audiobooks: an NVIDIA GPU with CUDA, an XTTS v2 model in `models/xtts_narrator/` (with `model.pth`) and a reference voice `.wav` in `voices/`
- An Audiobookshelf server and an API token, if you want to publish

## Setup

```bash
uv sync --extra tts --extra web
```

- `tts` installs the audio stack (PyTorch with CUDA, Coqui XTTS, Whisper).
- `web` installs the local web interface.
- Without extras, only the downloads (CBZ/PDF) and the terminal menus work.

On Windows, if uv complains about hard links, set `UV_LINK_MODE=copy`.

Then create your configuration:

1. Copy `config.example.toml` to `config.toml` and adjust paths, outputs and the Audiobookshelf section. Anything left out uses the defaults.
2. Put the Audiobookshelf API token in `.env`:

   ```
   ABS_TOKEN=your-token
   ```

The terminal menu **Configs ABS** can also fill in the server address, the token, the SSH access and the default library, and test the connection.

## Usage

```bash
uv run novel-narrator
```

Opens the terminal menus. From there you can:

- **CentralNovel:** search for a novel or paste its link, choose volumes or chapters, then choose the outputs.
- **EPUB local:** pick a `.epub`, choose the chapters, and publish it as a single audiobook in a library you choose.
- **Configs ABS:** set up and test the Audiobookshelf connection.

```bash
uv run novel-narrator-web
```

Serves the web interface on http://127.0.0.1:8000 and opens it in the browser. Jobs run one at a time in a queue, and each one can be paused and resumed.

## Configuration

`config.toml` sections:

| Section | What it controls |
|---|---|
| `paths` | Data, voices, model, PDF and CBZ folders; `tts_python` points to another Python for the TTS workers |
| `outputs` | Which outputs come pre-selected (`audiobook`, `cbz`, `pdf`) |
| `download` | Delay between requests, retries, parallel downloads, how many chapters downloads may run ahead of the audio |
| `text` | Segment ending, pauses and normalization rules |
| `tts` | Voice, device, number of workers and VRAM limits, QA attempts, XTTS sampling, Whisper verification |
| `audiobook` | Format, bitrate, language, how many chapters per upload batch, volume title, default author |
| `abs` | Audiobookshelf URL and library, plus either `local_dir` or `ssh` + `remote_dir` |

Generated files live in `data/`:

- `work/`: segments being generated; this is what makes resuming possible.
- `qa/`: reports for segments with alerts.
- `texts/`: chapter texts for the volume EPUBs.
- `outbox/`: files waiting to be sent.
- `logs/`: lists of failed chapters.

## Development

```bash
uv run ruff check
uv run ruff format --check
uv run pytest
```

The tests use a fake TTS worker, so they need neither a GPU nor the `tts` extra.

## License

This project is licensed under the [GNU Affero General Public License v3.0 or later](LICENSE). It depends on PyMuPDF, which is AGPL-3.0, and it ships a web interface, so the AGPL keeps the whole program under compatible terms.

Third-party pieces keep their own terms:

- The **XTTS v2 model weights** are under the [Coqui Public Model License](https://coqui.ai/cpml), which allows **non-commercial use only**. This also applies to models fine-tuned from them and to the audio they generate.
- **Novels downloaded with this tool belong to their authors and translators.** Use it only for content you are allowed to keep, for personal use, and respect each site's terms and request limits.

## Em português

O Novel Narrator baixa web novels em CBZ ou PDF e gera audiolivros narrados por uma voz XTTS ajustada, publicando no Audiobookshelf.

- Pode ser usado pelos menus do terminal (`uv run novel-narrator`) ou pela interface web (`uv run novel-narrator-web`).
- Antes de começar, mostra o que já existe e faz só o que falta.
- Exibe ao vivo a frase que está sendo convertida.
- Pausar libera a GPU na hora, e ao retomar continua do trecho em que parou.

A licença é a AGPL-3.0. Os pesos do XTTS só podem ser usados de forma não comercial, e as novels pertencem aos autores e tradutores.
