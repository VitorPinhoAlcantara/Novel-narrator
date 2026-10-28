# Novel Narrator

Download web novels and turn them into audiobooks narrated by a fine-tuned XTTS voice, then publish them to Audiobookshelf.

> Work in progress: this is the v2 rewrite of `centralnovel-downloader`.

## Features

- Novel sources behind one interface (CentralNovel, local EPUB)
- Audiobook generation with XTTS, per-segment emotion, automatic QA and Whisper verification
- CBZ output by default, PDF optional
- Publishing to Audiobookshelf over its API and SSH

## Requirements

- Python 3.12 and [uv](https://docs.astral.sh/uv/)
- NVIDIA GPU with CUDA for audiobook generation
- ffmpeg on `PATH`

## Setup

```bash
uv sync --extra tts --extra web
```

`tts` adds the audio stack (PyTorch, XTTS, Whisper) and `web` the local web interface. Copy `config.example.toml` to `config.toml` and put the Audiobookshelf token in `.env` as `ABS_TOKEN=...`.

## Usage

```bash
uv run novel-narrator
uv run novel-narrator-web
```

The first opens the terminal menus; the second serves the web interface on http://127.0.0.1:8000.

## Development

```bash
uv run ruff check
uv run ruff format --check
uv run pytest
```

## Em português

Baixa web novels e gera audiolivros narrados por uma voz XTTS ajustada, publicando no Audiobookshelf. A interface do programa é em português.
