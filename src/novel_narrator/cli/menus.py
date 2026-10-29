import copy
import os
import zipfile
from datetime import datetime

import requests
from InquirerPy import inquirer

from novel_narrator.cli.panel import Panel
from novel_narrator.config import load_config, save_config, save_env
from novel_narrator.log import format_duration, summarize_chapters
from novel_narrator.outputs.files import fetch_chapter_files, file_name, needs_files, split_downloaded
from novel_narrator.pipeline.audiobook import AudiobookSession
from novel_narrator.pipeline.runner import describe_outputs, run_chapters
from novel_narrator.publish import abs_api, abs_server
from novel_narrator.sources.base import SourceError
from novel_narrator.sources.centralnovel import CentralNovel
from novel_narrator.sources.epub import EpubSource, estimated_duration_s

ALL = "__all__"


def main_menu():
    while True:
        clear_screen()
        choice = inquirer.select(
            message="NOVEL NARRATOR",
            choices=[
                {"name": "CentralNovel (baixar novels: CBZ, PDF, audiolivro)", "value": "centralnovel"},
                {"name": "EPUB local (gerar audiolivro de um arquivo .epub)", "value": "epub"},
                {"name": "Configs ABS (servidor e biblioteca)", "value": "abs"},
                {"name": "Sair", "value": "quit"},
            ],
            cycle=True,
        ).execute()
        if choice == "centralnovel":
            novel_menu()
        elif choice == "epub":
            epub_menu()
        elif choice == "abs":
            abs_menu()
        else:
            break
    clear_screen()
    print("Encerrando...")


def novel_menu():
    config = load_config()
    source = CentralNovel(retries=config["download"]["max_retries"])
    while True:
        clear_screen()
        reference = select_novel(source)
        if not reference:
            return
        clear_screen()
        print(f"Carregando capitulos de: {reference.title}")
        try:
            novel = source.novel(reference.url)
            chapters = source.chapters(novel)
        except SourceError as exc:
            print(f"[ERRO] {exc}")
            chapters = []
        if not chapters:
            inquirer.confirm(message="Nenhum capitulo encontrado. Voltar?", default=True).execute()
            continue
        process_novel(source, novel, chapters, config)


def process_novel(source, novel, chapters, config):
    previous_outputs = None
    while True:
        selected = select_chapters_or_volumes(chapters)
        if selected is None:
            return
        if not selected:
            if not inquirer.confirm(message="Nenhum capitulo selecionado. Tentar novamente?", default=True).execute():
                return
            continue

        outputs = ask_outputs(config, previous_outputs)
        previous_outputs = outputs
        new_session = None
        audio_done = set()
        if "audio" in outputs:
            probe = AudiobookSession(source, novel, config)
            error = probe.prepare()
            if error:
                print(f"[ERRO] {error}")
                if not inquirer.confirm(message="Tentar novamente?", default=True).execute():
                    return
                continue
            audio_done = {chapter_key(c) for c in selected if not probe.pending(c)}
            new_session = session_factory(probe, lambda: AudiobookSession(source, novel, config))

        plan = resolve_existing(config, novel.title, selected, outputs, audio_done)
        if plan is None:
            continue
        selected, overwrite = plan

        clear_screen()
        print(f"Novel: {novel.title}")
        print(f"Capitulos selecionados: {len(selected)}")
        print(f"Volumes: {', '.join(list_volumes(selected))}")
        print(f"Saidas: {describe_outputs(outputs)}")
        if overwrite:
            print("Arquivos ja existentes serao sobrescritos")
        if not inquirer.confirm(message="Continuar?", default=True).execute():
            continue

        def run(chapters_to_run, outputs=outputs, overwrite=overwrite, new_session=new_session):
            download = config["download"]
            with Panel():
                return run_chapters(
                    chapters_to_run,
                    novel.title,
                    outputs,
                    fetch=lambda chapter: fetch_chapter_files(source, config, novel.title, chapter, outputs, overwrite),
                    audio=new_session() if new_session else None,
                    overwrite=overwrite,
                    parallel=download["parallel"],
                    lookahead=download["lookahead_chapters"],
                    delay_s=0.3,
                    needs_download=lambda chapter: needs_files(config, novel.title, chapter, outputs, overwrite),
                )

        handle_failures(config, novel.title, run(selected)["failures"], run)


def epub_menu():
    config = load_config()
    clear_screen()
    path = ask_epub()
    if not path:
        return
    try:
        source = EpubSource(path)
    except (zipfile.BadZipFile, KeyError, ValueError, OSError, AttributeError, TypeError) as exc:
        print(f"[ERRO] Nao foi possivel ler o EPUB: {exc}")
        inquirer.confirm(message="Voltar", default=True).execute()
        return
    novel, chapters = source.novel(), source.chapters()
    if not chapters:
        print("[ERRO] Nenhum texto encontrado nesse EPUB.")
        inquirer.confirm(message="Voltar", default=True).execute()
        return

    clear_screen()
    print(f"Livro: {novel.title}")
    print(f"Autor: {novel.author or '(nao informado no EPUB)'}")
    print(f"Capa: {'sim' if source.cover() else 'nao'} | Capitulos com texto: {len(chapters)}")
    print("Ja vem marcado o que parece ser conteudo; sumario, indice, notas e paginas curtas ficam desmarcados.\n")
    chosen = checkbox_with_all(
        "Capitulos para virar audio",
        [
            {
                "name": f"{c.number:>3}. {c.title[:60]}  (~{format_duration(estimated_duration_s([c]))})",
                "value": c,
                "enabled": c.suggested,
            }
            for c in chapters
        ],
        f"[ Todos os {len(chapters)} capitulos ]",
        validate=lambda marked: len(marked) > 0,
        invalid_message="Selecione pelo menos um capitulo",
    )
    if not chosen:
        return

    destination = choose_library(config, "Biblioteca do Audiobookshelf onde o livro vai aparecer")
    if destination is None:
        return
    run_config = copy.deepcopy(config)
    run_config["abs"].update(destination)

    probe = AudiobookSession(source, novel, run_config, book_mode=True)
    error = probe.prepare()
    if error:
        print(f"[ERRO] {error}")
        inquirer.confirm(message="Voltar", default=True).execute()
        return
    audio_done = {chapter_key(c) for c in chosen if not probe.pending(c)}
    plan = resolve_existing(run_config, novel.title, chosen, {"audio"}, audio_done)
    if plan is None:
        return
    chosen, overwrite = plan
    audio_s = estimated_duration_s(chosen)
    abs_cfg = run_config["abs"]

    clear_screen()
    print(f"Livro: {novel.title} | Autor: {probe.author}")
    place = abs_cfg["remote_dir"] if abs_server.is_remote(abs_cfg) else abs_cfg["local_dir"]
    print(f"Destino: biblioteca {abs_cfg['library']} ({place})")
    print(
        f"Capitulos: {len(chosen)} | audio estimado: ~{format_duration(audio_s)} | "
        f"tempo de geracao estimado: ~{format_duration(audio_s * 0.75)}"
    )
    print("No Audiobookshelf ele aparece como um livro, com um arquivo por capitulo, a capa e o EPUB.")
    if overwrite:
        print("Audios ja existentes serao sobrescritos")
    if not inquirer.confirm(message="Gerar o audiolivro?", default=True).execute():
        return

    new_session = session_factory(probe, lambda: AudiobookSession(source, novel, run_config, book_mode=True))

    def run(chapters_to_run):
        with Panel():
            return run_chapters(chapters_to_run, novel.title, {"audio"}, audio=new_session(), overwrite=overwrite)

    handle_failures(run_config, novel.title, run(chosen)["failures"], run)
    inquirer.confirm(message="Voltar ao menu principal", default=True).execute()


def abs_menu():
    while True:
        config = load_config()
        abs_cfg = config["abs"]
        clear_screen()
        print("CONFIGS DO AUDIOBOOKSHELF")
        print("=" * 50)
        print(f"Endereco (URL):       {abs_cfg['url'] or '(nao definido)'}")
        print(f"Token de API:         {mask(abs_cfg['token'])}")
        print(f"Acesso SSH:           {abs_cfg['ssh'] or '(sem SSH: grava na pasta local)'}")
        if abs_server.is_remote(abs_cfg):
            print(f"Pasta no servidor:    {abs_cfg['remote_dir']}")
        else:
            print(f"Pasta local:          {abs_cfg['local_dir'] or '(nao definida)'}")
        print(f"Biblioteca padrao:    {abs_cfg['library'] or '(nao definida)'}  (usada pela CentralNovel)")
        print("=" * 50)
        choice = inquirer.select(
            message="O que alterar?",
            choices=[
                {"name": "Biblioteca padrao (lista as bibliotecas do servidor)", "value": "library"},
                {"name": "Endereco do servidor (URL)", "value": "url"},
                {"name": "Token de API", "value": "token"},
                {"name": "Acesso SSH (usuario@host)", "value": "ssh"},
                {"name": "Testar conexao", "value": "test"},
                {"name": "Voltar", "value": "back"},
            ],
            cycle=True,
        ).execute()
        if choice == "back":
            return
        if choice == "library":
            changes = choose_library(config, "Biblioteca padrao")
            if changes:
                save_config({"abs": changes})
                print("Salvo.")
        elif choice == "url":
            url = (
                inquirer.text(
                    message="Endereco do Audiobookshelf (ex.: http://192.168.0.10:13378):",
                    default=abs_cfg["url"],
                    validate=lambda t: t.strip().startswith(("http://", "https://")),
                    invalid_message="Comece com http:// ou https://",
                )
                .execute()
                .strip()
                .rstrip("/")
            )
            if test_api(abs_cfg, url=url) or confirm_anyway("Nao respondeu. Salvar mesmo assim?"):
                save_config({"abs": {"url": url}})
                print("Salvo.")
        elif choice == "token":
            token = (
                inquirer.secret(message="Novo token (ABS > Configuracoes > Usuarios > seu usuario > API token):")
                .execute()
                .strip()
            )
            if token and (test_api(abs_cfg, token=token) or confirm_anyway("Token recusado. Salvar mesmo assim?")):
                save_env("ABS_TOKEN", token)
                print("Salvo no .env.")
        elif choice == "ssh":
            ssh = (
                inquirer.text(
                    message="Acesso SSH (usuario@host; vazio = gravar em pasta local):", default=abs_cfg["ssh"]
                )
                .execute()
                .strip()
            )
            error = abs_server.check_destination({**abs_cfg, "ssh": ssh}) if ssh else None
            if error:
                print(f"[AVISO] {error}")
            if not error or confirm_anyway("Salvar mesmo assim?"):
                save_config({"abs": {"ssh": ssh}})
                print("Salvo.")
        elif choice == "test":
            test_everything(abs_cfg)
        inquirer.confirm(message="Continuar", default=True).execute()


def select_novel(source):
    try:
        popular = source.popular(limit=10)
    except SourceError as exc:
        print(f"[AVISO] Nao foi possivel carregar as mais populares: {exc}")
        popular = []
    choices = [{"name": f"{i}. {n.title}", "value": n} for i, n in enumerate(popular, 1)]
    choices += [
        {"name": "Buscar por nome", "value": "search"},
        {"name": "Informar link da novel", "value": "link"},
        {"name": "Voltar", "value": None},
    ]
    choice = inquirer.select(
        message="Escolha uma novel", choices=choices, cycle=True, height=min(20, len(choices) + 2)
    ).execute()
    if choice == "link":
        return select_novel_by_link(source)
    if choice == "search":
        return select_novel_by_search(source)
    return choice


def select_novel_by_link(source):
    while True:
        text = inquirer.text(message="Cole o link da novel (ou vazio para voltar):").execute().strip()
        if not text:
            return None
        url = source.parse_url(text)
        if url:
            try:
                return source.novel(url)
            except SourceError as exc:
                print(f"[ERRO] {exc}")
        if not inquirer.confirm(message="Link invalido. Tentar novamente?", default=True).execute():
            return None


def select_novel_by_search(source):
    query = inquirer.text(message="Digite o nome da novel (ou vazio para voltar):").execute().strip()
    if not query:
        return None
    try:
        results = source.search(query, limit=20)
    except SourceError as exc:
        print(f"[ERRO] {exc}")
        results = []
    if not results:
        inquirer.confirm(message="Nenhuma novel encontrada. Voltar?", default=True).execute()
        return None
    return inquirer.select(
        message="Resultados da busca",
        choices=[{"name": n.title, "value": n} for n in results] + [{"name": "Voltar", "value": None}],
        cycle=True,
        height=min(20, len(results) + 3),
    ).execute()


def select_chapters_or_volumes(chapters):
    mode = inquirer.select(
        message="Selecione o tipo de download",
        choices=[
            {"name": "Capitulos especificos", "value": "chapters"},
            {"name": "Volumes completos", "value": "volumes"},
            {"name": "Voltar (escolher outra novel)", "value": "back"},
        ],
        cycle=True,
    ).execute()
    if mode == "back":
        return None
    if mode == "chapters":
        return select_chapters(chapters)
    return select_volumes(chapters)


def select_chapters(chapters):
    numbers = sorted({c.number for c in chapters})
    text = (
        inquirer.text(message=f"Digite os capitulos ({numbers[0]} a {numbers[-1]}), ex: 1,2,10-15. Vazio cancela:")
        .execute()
        .strip()
    )
    if not text:
        return []
    try:
        wanted = set(parse_numbers(text))
    except ValueError:
        inquirer.confirm(message="Formato invalido. Voltar?", default=True).execute()
        return []
    return [c for c in chapters if c.number in wanted]


def select_volumes(chapters):
    volumes = list_volumes(chapters)
    chosen = checkbox_with_all(
        "Selecione um ou mais volumes",
        [{"name": v, "value": v} for v in volumes],
        f"[ Todos os {len(volumes)} volumes ]",
        max_height=20,
    )
    return [c for c in chapters if c.volume in set(chosen)] if chosen else []


def checkbox_with_all(message, options, all_label, max_height=25, **extra):
    chosen = inquirer.checkbox(
        message=f"{message} (espaco marca, ctrl+a marca todos, ctrl+r inverte, enter confirma)",
        choices=[{"name": all_label, "value": ALL}] + options,
        cycle=True,
        height=min(max_height, len(options) + 3),
        **extra,
    ).execute()
    if any(isinstance(item, str) and item == ALL for item in chosen):
        return [option["value"] for option in options]
    return chosen


def ask_outputs(config, previous=None):
    configured = {"audio" if k == "audiobook" else k for k, on in config["outputs"].items() if on}
    default = set(previous) if previous else configured
    options = [("cbz", "CBZ"), ("pdf", "PDF"), ("audio", "Audiolivro (Audiobookshelf)")]
    return set(
        inquirer.checkbox(
            message="O que gerar? (espaco marca, enter confirma)",
            choices=[{"name": name, "value": value, "enabled": value in default} for value, name in options],
            validate=lambda chosen: len(chosen) > 0,
            invalid_message="Selecione pelo menos uma opcao",
            cycle=True,
        ).execute()
    )


def resolve_existing(config, novel_title, chapters, outputs, audio_done):
    files_done, _ = split_downloaded(config, novel_title, chapters, outputs & {"pdf", "cbz"})
    files_keys = {chapter_key(c) for c in files_done}
    done_keys = {k for k in files_keys if "audio" not in outputs or k in audio_done}
    existing = [c for c in chapters if chapter_key(c) in done_keys]
    missing = [c for c in chapters if chapter_key(c) not in done_keys]
    if not existing:
        return chapters, False

    clear_screen()
    print(f"{len(existing)} de {len(chapters)} capitulos ja possuem as saidas escolhidas:")
    print(f"  {summarize_chapters(existing)}")
    if missing:
        print(f"Faltam {len(missing)}: {summarize_chapters(missing)}")
    print()
    choices = []
    if missing:
        choices.append({"name": f"Processar apenas os faltantes ({len(missing)})", "value": (missing, False)})
    choices.append({"name": f"Reescrever os ja existentes ({len(existing)})", "value": (existing, True)})
    if missing:
        choices.append({"name": f"Ambos: faltantes + reescrever ({len(chapters)})", "value": (chapters, True)})
    choices.append({"name": "Cancelar", "value": None})
    return inquirer.select(message="Alguns capitulos ja existem. O que fazer?", choices=choices, cycle=True).execute()


def handle_failures(config, novel_title, failures, run):
    log_path = None
    while failures:
        log_path = write_failure_log(config, novel_title, failures, log_path)
        print(f"\n{len(failures)} capitulo(s) com erro: {summarize_chapters([f['chapter'] for f in failures])}")
        print(f"Log: {log_path}")
        if not inquirer.confirm(message="Tentar novamente os que falharam?", default=True).execute():
            return
        failures = run([f["chapter"] for f in failures])["failures"]
    if log_path:
        os.remove(log_path)
        print("\nTodos os capitulos com erro foram recuperados (log removido).")


def write_failure_log(config, novel_title, failures, path=None):
    if path is None:
        folder = os.path.join(config["paths"]["data_dir"], "logs")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"falhas_{file_name(novel_title) or 'novel'}_{datetime.now():%Y%m%d_%H%M%S}.log")
    chapters = [f["chapter"] for f in failures]
    lines = [
        f"Novel: {novel_title}",
        f"Gerado em: {datetime.now():%Y-%m-%d %H:%M:%S}",
        f"Capitulos com erro: {len(failures)}",
        f"Resumo: {summarize_chapters(chapters)}",
        f"Para refazer (menu Capitulos especificos): {', '.join(str(n) for n in sorted({c.number for c in chapters}))}",
        "",
    ]
    for failure in failures:
        chapter = failure["chapter"]
        lines.append(f"Vol. {chapter.volume} | Cap. {chapter.number} | {chapter.title} | {failure['reason']}")
        lines.append(f"    {chapter.url}")
    with open(path, "w", encoding="utf-8") as file:
        file.write("\n".join(lines) + "\n")
    return path


def choose_library(config, message):
    abs_cfg = config["abs"]
    try:
        libraries = abs_api.list_libraries(abs_cfg)
    except requests.RequestException as exc:
        print(f"[ERRO] Nao foi possivel listar as bibliotecas do Audiobookshelf: {exc}")
        print("Confira o endereco e o token em Configs ABS.")
        inquirer.confirm(message="Voltar", default=True).execute()
        return None
    if not libraries:
        print("[ERRO] O Audiobookshelf nao tem nenhuma biblioteca.")
        inquirer.confirm(message="Voltar", default=True).execute()
        return None

    default = next((lib for lib in libraries if lib["name"] == abs_cfg["library"]), libraries[0])
    chosen = inquirer.select(
        message=message,
        choices=[
            {"name": f"{lib['name']}  (pasta: {', '.join(lib['folders']) or '?'})", "value": lib} for lib in libraries
        ]
        + [{"name": "Cancelar", "value": None}],
        default=default,
        cycle=True,
    ).execute()
    if chosen is None:
        return None
    if not chosen["folders"]:
        print("[ERRO] Essa biblioteca nao tem pasta configurada no Audiobookshelf.")
        inquirer.confirm(message="Voltar", default=True).execute()
        return None
    folder = chosen["folders"][0]
    if len(chosen["folders"]) > 1:
        folder = inquirer.select(message="Pasta da biblioteca", choices=chosen["folders"]).execute()

    others = abs_api.libraries_sharing_folder(libraries, chosen, folder)
    if others:
        print(f"\n[AVISO] A pasta {folder} tambem e usada pela(s) biblioteca(s): {', '.join(others)}.")
        print("Tudo o que for enviado aparece nas duas. Para separar, cada biblioteca precisa de uma pasta propria.")
        if not confirm_anyway("Continuar mesmo assim?"):
            return None

    changes = {"library": chosen["name"]}
    if abs_server.is_remote(abs_cfg):
        on_server = abs_api.server_folder(folder, abs_api.detect_mounts(abs_cfg))
        if on_server is None:
            print(f"Nao consegui descobrir automaticamente onde fica {folder} no servidor.")
            on_server = (
                inquirer.text(message=f"Caminho no servidor que corresponde a {folder}:", default=abs_cfg["remote_dir"])
                .execute()
                .strip()
            )
            if not on_server:
                return None
        changes["remote_dir"] = on_server
    return changes


def test_api(abs_cfg, url=None, token=None):
    try:
        libraries = abs_api.list_libraries(abs_cfg, url=url, token=token)
    except requests.RequestException as exc:
        print(f"[FALHA] API do Audiobookshelf: {exc}")
        return False
    print(f"[OK] API respondeu: {len(libraries)} biblioteca(s): {', '.join(lib['name'] for lib in libraries)}")
    return True


def test_everything(abs_cfg):
    test_api(abs_cfg)
    error = abs_server.check_destination(abs_cfg)
    if not abs_server.is_remote(abs_cfg):
        print(f"[{'FALHA' if error else 'OK'}] Pasta local: {error or abs_cfg['local_dir']}")
        return
    print(f"[{'FALHA' if error else 'OK'}] SSH e pasta no servidor: {error or abs_cfg['remote_dir']}")
    mounts = abs_api.detect_mounts(abs_cfg)
    if not mounts:
        print("[AVISO] Nao consegui ler as pastas do container (docker inspect via SSH).")
        return
    print("[OK] Pastas do container detectadas:")
    for inside, outside in sorted(mounts.items()):
        print(f"       {inside}  ->  {outside}")


def ask_epub():
    print("Abrindo a janela para escolher o arquivo .epub...")
    path = file_picker()
    if path:
        return path
    print("Nenhum arquivo escolhido na janela.")
    path = clean_path(
        inquirer.filepath(
            message="Arraste o arquivo .epub para esta janela ou cole o caminho (vazio volta):",
            validate=lambda text: clean_path(text) == "" or is_epub(clean_path(text)),
            invalid_message="Informe um arquivo .epub que exista",
        ).execute()
    )
    return path or None


def file_picker():
    try:
        import tkinter
        from tkinter import filedialog

        root = tkinter.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        try:
            path = filedialog.askopenfilename(
                parent=root,
                title="Escolha o arquivo .epub",
                initialdir=os.path.join(os.path.expanduser("~"), "Downloads"),
                filetypes=[("Livros EPUB", "*.epub"), ("Todos os arquivos", "*.*")],
            )
        finally:
            root.destroy()
    except Exception:
        return None
    path = os.path.normpath(path) if path else ""
    return path if is_epub(path) else None


def clean_path(text):
    text = text.strip()
    if text.startswith("& "):
        text = text[2:].strip()
    return text.strip("'\"").strip()


def is_epub(path):
    return bool(path) and path.lower().endswith(".epub") and os.path.isfile(path)


def parse_numbers(text):
    values = set()
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start, end = sorted(int(x.strip()) for x in part.split("-", 1))
            values.update(range(start, end + 1))
        else:
            values.add(int(part))
    return sorted(values)


def list_volumes(chapters):
    return sorted({c.volume for c in chapters}, key=volume_order)


def volume_order(volume):
    text = str(volume).strip()
    return (0, int(text)) if text.isdigit() else (1, " ".join(text.lower().split()))


def chapter_key(chapter):
    return (str(chapter.volume), chapter.number)


def session_factory(first, make):
    pending = [first]
    return lambda: pending.pop() if pending else make()


def mask(token):
    return f"definido (termina em ...{token[-4:]})" if token else "(nao definido)"


def confirm_anyway(message):
    return inquirer.confirm(message=message, default=False).execute()


def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")
