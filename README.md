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
uv sync
uv sync --extra tts
```

The first command installs the app; the second adds the TTS stack (PyTorch, XTTS, Whisper).

## Development

```bash
uv run ruff check
uv run ruff format --check
uv run pytest
```

## Em português

Baixa web novels e gera audiolivros narrados por uma voz XTTS ajustada, publicando no Audiobookshelf. A interface do programa é em português.
