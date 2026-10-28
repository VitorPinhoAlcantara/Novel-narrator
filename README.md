# Centralnovel Downloader

Projeto em Python para:
- baixar capitulos de qualquer novel da Centralnovel
- selecionar capitulos especificos ou volumes completos
- converter PDF para CBZ no mesmo fluxo de download

## Execucao principal

Use o arquivo unico `main.py`:

```powershell
python main.py
```

A navegacao agora e interativa no terminal (setas/espaco/enter), com limpeza de tela ao trocar de menu.

Menu principal:
1. **CentralNovel**: baixar novels do site (PDF, CBZ e/ou audiolivro)
2. **EPUB local**: gerar audiolivro a partir de um arquivo `.epub` do computador
3. **Configs ABS**: endereco, token, acesso SSH e biblioteca padrao do Audiobookshelf
4. Sair

## EPUB local

Escolha o arquivo `.epub` (Tab completa o caminho). O programa le titulo, autor, capa e o sumario, e lista os capitulos com a duracao estimada; ja vem marcado o que parece conteudo (sumario, indice, notas, ficha catalografica e paginas curtas ficam desmarcados). Depois escolha a biblioteca do Audiobookshelf de destino. No servidor o livro fica em `<Autor>/<Livro>/001 - <Capitulo>.m4b`, com a capa e o proprio EPUB na pasta, e o ABS mostra um livro com um arquivo por capitulo. Notas de rodape (numeros sobrescritos) nao sao lidas. Interromper e retomar funciona igual ao das novels.

## Configs ABS

Mostra a configuracao atual e permite trocar o endereco, o token, o acesso SSH e a biblioteca padrao (usada pela CentralNovel). As bibliotecas sao listadas pela API do Audiobookshelf, e a pasta do servidor correspondente a cada uma e descoberta automaticamente lendo as montagens do container (`docker inspect` via SSH). Se duas bibliotecas usarem a mesma pasta, o programa avisa: tudo o que for enviado aparece nas duas. "Testar conexao" confere a API, o SSH e as pastas detectadas.

## Fluxo de download

No menu de download:
1. lista top 10 novels do site
2. voce escolhe por numero, nome da novel ou link da novel
3. escolhe baixar capitulos especificos ou volumes completos (aceita varios: `1,2,10-15`)
4. marca o que gerar: PDF, CBZ e/ou Audiolivro

## Recursos do download

- **Ja baixados**: antes de baixar, o programa verifica cada capitulo (PDF, e CBZ se escolhido). Se algum ja existir, voce escolhe: baixar so os faltantes, reescrever os existentes, ambos ou cancelar.
- **Falhas**: ao final, os capitulos com erro sao listados e voce pode tentar novamente (repetivel). O log com capitulo, motivo e URL fica em `logs/falhas_<novel>_<data>.log` (removido se tudo for recuperado).
- **Limpeza do PDF**: o link do site e a marca "Traduzido usando o ChatGPT" (ou similar) sao removidos da primeira pagina automaticamente. Ajuste em `centralnovel/config.py` (`REMOVER_LINK_PDF`, `REMOVER_MARCA_TRADUCAO`). Para PDFs ja baixados: Conversao PDF -> CBZ > "Limpar PDFs".

## Pastas de saida (padrao)

Todos os arquivos ficam separados por novel e volume:

```text
PDF/<Novel>/Vol_XX/*.pdf
CBZ/<Novel>/Vol_XX/*.cbz
```

## Instalar dependencias

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Estrutura nova

```text
centralnovel-downloader/
├── main.py
├── centralnovel/
│   ├── __init__.py
│   ├── config.py
│   ├── csv_store.py
│   ├── download_utils.py
│   ├── downloader.py
│   ├── scraper.py
│   ├── converter.py
│   └── menus.py
├── requirements.txt
│   └── selection.py
├── Backup/
│   ├── download_pdfs.py
│   └── pdf_to_cbz.py
├── PDF/
├── CBZ/
└── links_capitulos.csv
```

## Ajustes de configuracao

Edite `centralnovel/config.py`:
- `DELAY_ENTRE_DOWNLOADS`
- `MAX_RETRIES`
- `QUALIDADE_JPG`
- `DPI`
- `PDF_ROOT_DIR`
- `CBZ_ROOT_DIR`

## Saidas do download

No menu de download voce marca o que gerar (espaco marca, enter confirma), em qualquer combinacao:

- **PDF**
- **CBZ** (se o PDF nao foi marcado, o PDF baixado so como etapa intermediaria e apagado depois do CBZ; um PDF que ja existia e preservado)
- **Audiolivro (Audiobookshelf)**

Com **Audiolivro** marcado, os downloads (PDF/CBZ) sao feitos um por vez e no maximo 6 capitulos a frente do audio (`MAX_CAPITULOS_A_FRENTE` em `centralnovel/config.py`): o audio comeca assim que o primeiro capitulo e baixado e o proximo download so acontece quando um dos anteriores termina o audio. Capitulos que ja tem audio no servidor so geram o PDF/CBZ que faltar. Sem audiolivro, os downloads continuam em paralelo. O item "Gerar audiolivro" do menu principal e um atalho com o audiolivro ja marcado.

## Audiolivro (XTTS + Audiobookshelf)

O motor e proprio (pacote `centralnovel/tts/`), sem ebook2audiobook. Fluxo por capitulo:

1. o texto e lido direto do HTML do site (sem PDF), sem o link e a marca de traducao
2. normalizacao PT-BR (abreviacoes, numeros por extenso, reticencias; interjeicoes mantidas, pois o modelo ajustado as pronuncia) e divisao em trechos de 60-180 caracteres
3. `workers` processos XTTS geram os trechos em paralelo na GPU (modelo carregado uma vez); o ponto final do trecho vira ` ;` para o XTTS nao falar "ponto"
4. QA numerico (duracao, silencio, energia) e verificacao por transcricao com Whisper: trechos com palavras puladas ou "ponto" falado sao regenerados com outra seed
5. os trechos sao montados com pausas, normalizados (loudnorm) e gravados em m4b com as tags corretas
6. a cada `lote_capitulos` os audios sao enviados ao servidor do Audiobookshelf (SSH, com conferencia de tamanho) junto com um EPUB do volume (texto dos capitulos) e e pedido um scan da biblioteca

Estrutura gravada (o Audiobookshelf junta os arquivos da pasta em um unico livro e reconhece a serie pelo caminho):

```
<abs_dir>/<Autor>/<Novel>/Vol. 2 - <Novel> Volume 2/0096 - Capitulo 96 - Exilio.m4b
<abs_dir>/<Autor>/<Novel>/Vol. 2 - <Novel> Volume 2/<Novel> Volume 2.epub
```

Capitulos que ja existem no servidor sao ignorados.

**Interrupcao (Ctrl+C).** O capitulo em andamento nao e enviado ao servidor: o `.m4b` so existe depois de pronto (e gravado como `.part` e renomeado no fim). Os trechos ja gerados ficam guardados em `audiobook_work/<Novel>/cap_<volume>_<numero>/`, e os capitulos que ja estavam concluidos mas aguardavam o envio em lote sao enviados na saida. Para continuar, selecione o volume de novo e escolha **"Processar apenas os faltantes"**: o capitulo interrompido nao esta no servidor, entao volta a ser pendente e retoma dos trechos guardados (os que ja foram gerados mas nao verificados pelo Whisper so sao verificados, nao regenerados). Audios incompletos que tenham sobrado na pasta de espera sao descartados para `audiobook_work/descartados/` antes de qualquer envio.

### Ambiente do TTS

O worker roda no Python do ambiente `tts_env/` (torch cu130, para GPUs Blackwell). Para criar:

```powershell
python -m venv tts_env
.\tts_env\Scripts\python.exe -m pip install -r requirements-tts.txt
```

Arquivos locais (ignorados pelo git): `modelos/xtts_narrador` (modelo ajustado, usado por padrao), `modelos/xtts_base` (XTTS-v2 original, base do retreino) e `vozes/` (referencias `.wav`). O Whisper (`asr_modelo`) e baixado do Hugging Face na primeira verificacao.

### Configuracao

Na primeira execucao e criado `audiobook_config.json` (fora do git). Ajuste:

- `tts_python` (padrao `tts_env`), `xtts_model_dir` (padrao `modelos/xtts_narrador`), `workers` (maximo; 3 e ~2,5x mais rapido que 1 e usa ~12 GB de VRAM), `workers_adaptativo` (liga o ajuste ao vivo: antes de cada capitulo mede o uso da GPU sem os workers e usa 3 com uso baixo, 2 com moderado e 1 com alto, limitado pela VRAM livre; durante o capitulo pausa workers se a VRAM livre ficar critica), `gpu_util_baixo`, `gpu_util_moderado`, `vram_por_worker_mb`, `vram_reserva_mb`, `vram_critica_mb`, `vram_folga_mb`, `matmul`
- `voz_dir`, `voz_padrao`: pasta dos `.wav` de referencia; o fluxo sempre usa `voz_padrao` com o modelo ajustado (nao ha mais escolha de voz no menu)
- `xtts`: temperatura, repetition penalty, top-k, top-p e speed; `xtts_ref`: tamanho da referencia de voz (`gpt_cond_len` 30 s soa mais expressivo que o padrao de 6 s)
- `qa`, `qa_tentativas`, `asr`, `asr_modelo`, `asr_rodadas`, `final_trecho`, `normalizacao`, `pausas_ms`, `bitrate`
- `abs_ssh`, `abs_remote_dir`: servidor do Audiobookshelf acessado por SSH (ex.: `topiinho@server`) e a pasta de livros nele; deixe `abs_ssh` vazio para gravar numa pasta local (`abs_dir`)
- `abs_url`, `abs_token` (ou variavel de ambiente `ABS_TOKEN`), `abs_biblioteca`: usados para pedir o scan
- `lote_capitulos`: a cada quantos capitulos enviar ao servidor e escanear

Relatorios de QA por capitulo ficam em `audiobook_work/qa/`. Experimentos de qualidade (A/B de texto, paralelismo) estao em `scripts/tts_lab/`.
