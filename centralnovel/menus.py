"""Interactive TUI menus for downloader and converter."""

import os
import zipfile

import requests
from InquirerPy import inquirer

from .abs_api import bibliotecas_na_mesma_pasta, detectar_mapeamento, listar_bibliotecas, pasta_no_servidor
from .audiolivro import SessaoAudiolivro, separar_pendentes
from .audiolivro_config import carregar_config, salvar_config
from .config import DPI, QUALIDADE_JPG
from .converter import converter_pdf_para_cbz, processar_pasta
from .download_utils import gravar_log_falhas, resumir_capitulos
from .downloader import (
    chave_capitulo,
    descrever_saidas,
    download_capitulos_novel,
    separar_baixados,
)
from .epub_local import duracao_estimada_s, ler_epub
from .log import formatar_duracao
from .pdf_cleaner import limpar_pasta
from .scraper import (
    buscar_novels_por_nome,
    extrair_links_pdf,
    listar_top_novels,
    normalizar_url_novel,
    obter_titulo_novel,
)
from .selection import parse_numero_lista_ou_intervalo
from .servidor_abs import modo_remoto, verificar_destino
from .texto_capitulo import obter_info_novel


def menu_principal():
    while True:
        _clear_screen()
        escolha = inquirer.select(
            message="CENTRALNOVEL - MAIN",
            choices=[
                {"name": "CentralNovel (baixar novels: PDF, CBZ, audiolivro)", "value": "centralnovel"},
                {"name": "EPUB local (gerar audiolivro de um arquivo .epub)", "value": "epub"},
                {"name": "Configs ABS (servidor e biblioteca)", "value": "abs"},
                {"name": "Sair", "value": "sair"},
            ],
            cycle=True,
        ).execute()

        if escolha == "centralnovel":
            menu_download()
        elif escolha == "epub":
            menu_epub()
        elif escolha == "abs":
            menu_config_abs()
        else:
            break
    _clear_screen()
    print("Encerrando...")


def menu_download():
    config = None
    while True:
        _clear_screen()
        novel = _selecionar_novel()
        if not novel:
            return False

        _clear_screen()
        print(f"Carregando capitulos de: {novel['title']}")
        capitulos = extrair_links_pdf(novel["url"])
        if not capitulos:
            inquirer.confirm(message="Nenhum capitulo encontrado. Voltar?", default=True).execute()
            continue

        _processar_novel(novel, capitulos, config)


def _processar_novel(novel, capitulos, config):
    info = None
    saidas_anteriores = None
    while True:
        selecionados = _selecionar_capitulos_ou_volumes(capitulos)
        if selecionados is None:
            return
        if not selecionados:
            if not inquirer.confirm(
                message="Nenhum capitulo selecionado. Tentar novamente?",
                default=True,
            ).execute():
                return
            continue

        saidas = _perguntar_saidas(saidas_anteriores)
        saidas_anteriores = saidas
        criar_sessao = None
        audio_prontos = set()
        if "audio" in saidas:
            config = config or carregar_config()
            info = info or obter_info_novel(novel["url"])
            try:
                prontos, _ = separar_pendentes(selecionados, novel["title"], info, config)
            except RuntimeError as exc:
                print(f"[ERRO] Servidor do Audiobookshelf indisponivel: {exc}")
                if not inquirer.confirm(message="Tentar novamente?", default=True).execute():
                    return
                continue
            audio_prontos = {chave_capitulo(cap) for cap in prontos}
            voz = config["voz_padrao"]

            def criar_sessao(voz=voz, info=info, config=config):
                return SessaoAudiolivro(novel["title"], novel["url"], voz, config, info)

        plano = _resolver_ja_baixados(selecionados, novel["title"], saidas, audio_prontos)
        if plano is None:
            continue
        selecionados, sobrescrever = plano

        volumes = ", ".join(_listar_volumes(selecionados))
        _clear_screen()
        print(f"Novel: {novel['title']}")
        print(f"Capitulos selecionados: {len(selecionados)}")
        print(f"Volumes: {volumes}")
        print(f"Saidas: {descrever_saidas(saidas)}")
        if sobrescrever:
            print("Arquivos ja existentes serao sobrescritos")
        if not inquirer.confirm(message="Continuar?", default=True).execute():
            continue

        resultado = download_capitulos_novel(
            selecionados, novel["title"], saidas=saidas, sobrescrever=sobrescrever,
            sessao_audio=criar_sessao() if criar_sessao else None,
        )
        _tratar_falhas(resultado["falhas"], novel["title"], saidas, sobrescrever, criar_sessao)


def menu_epub():
    config = carregar_config()
    _clear_screen()
    caminho = _perguntar_epub()
    if not caminho:
        return
    try:
        livro = ler_epub(caminho)
    except (zipfile.BadZipFile, KeyError, ValueError, OSError, AttributeError, TypeError) as exc:
        print(f"[ERRO] Nao foi possivel ler o EPUB: {exc}")
        inquirer.confirm(message="Voltar", default=True).execute()
        return
    if not livro["capitulos"]:
        print("[ERRO] Nenhum texto encontrado nesse EPUB.")
        inquirer.confirm(message="Voltar", default=True).execute()
        return

    _clear_screen()
    print(f"Livro: {livro['titulo']}")
    print(f"Autor: {livro['autor'] or '(nao informado no EPUB)'}")
    print(f"Capa: {'sim' if livro['capa'] else 'nao'} | Capitulos com texto: {len(livro['capitulos'])}")
    print("Ja vem marcado o que parece ser conteudo; sumario, indice, notas e paginas curtas ficam desmarcados.\n")
    escolhidos = _checkbox_com_todos(
        "Capitulos para virar audio",
        [
            {
                "name": f"{c['indice']:>3}. {c['titulo'][:60]}  (~{formatar_duracao(duracao_estimada_s([c]))})",
                "value": c,
                "enabled": c["sugerido"],
            }
            for c in livro["capitulos"]
        ],
        f"[ Todos os {len(livro['capitulos'])} capitulos ]",
        validate=lambda marcados: len(marcados) > 0,
        invalid_message="Selecione pelo menos um capitulo",
    )
    if not escolhidos:
        return

    destino = _escolher_biblioteca(config, "Biblioteca do Audiobookshelf onde o livro vai aparecer")
    if destino is None:
        return
    cfg = dict(config)
    cfg.update(destino)

    titulo = livro["titulo"]
    capa = livro["capa"] or {}
    info = {
        "autor": livro["autor"] or cfg["autor_padrao"],
        "capa": "",
        "capa_dados": capa.get("dados"),
        "capa_extensao": capa.get("extensao", "jpg"),
        "epub_origem": caminho,
    }
    caps = [
        {"capitulo": str(c["indice"]), "titulo": c["titulo"], "volume": "", "url": "", "livro": True,
         "titulo_falado": "", "paragrafos": c["paragrafos"]}
        for c in escolhidos
    ]
    try:
        prontos, _ = separar_pendentes(caps, titulo, info, cfg)
    except RuntimeError as exc:
        print(f"[ERRO] Servidor do Audiobookshelf indisponivel: {exc}")
        inquirer.confirm(message="Voltar", default=True).execute()
        return
    plano = _resolver_ja_baixados(caps, titulo, {"audio"}, {chave_capitulo(c) for c in prontos})
    if plano is None:
        return
    caps, sobrescrever = plano
    selecionados = [c for c in escolhidos if str(c["indice"]) in {cap["capitulo"] for cap in caps}]
    audio_s = duracao_estimada_s(selecionados)

    _clear_screen()
    print(f"Livro: {titulo} | Autor: {info['autor']}")
    print(f"Destino: biblioteca {cfg['abs_biblioteca']} ({cfg['abs_remote_dir'] if modo_remoto(cfg) else cfg['abs_dir']})")
    print(f"Capitulos: {len(caps)} | audio estimado: ~{formatar_duracao(audio_s)} | "
          f"tempo de geracao estimado: ~{formatar_duracao(audio_s * 0.75)}")
    print("No Audiobookshelf ele aparece como um livro, com um arquivo por capitulo, a capa e o EPUB.")
    if sobrescrever:
        print("Audios ja existentes serao sobrescritos")
    if not inquirer.confirm(message="Gerar o audiolivro?", default=True).execute():
        return

    def criar_sessao():
        return SessaoAudiolivro(titulo, "", cfg["voz_padrao"], cfg, dict(info))

    resultado = download_capitulos_novel(
        caps, titulo, saidas={"audio"}, sobrescrever=sobrescrever, sessao_audio=criar_sessao()
    )
    _tratar_falhas(resultado["falhas"], titulo, {"audio"}, sobrescrever, criar_sessao)
    inquirer.confirm(message="Voltar ao menu principal", default=True).execute()


def _perguntar_epub():
    print("Abrindo a janela para escolher o arquivo .epub...")
    caminho = _seletor_de_arquivo()
    if caminho:
        return caminho

    print("Nenhum arquivo escolhido na janela.")
    caminho = _limpar_caminho(inquirer.filepath(
        message="Arraste o arquivo .epub para esta janela ou cole o caminho (vazio volta):",
        validate=lambda texto: _limpar_caminho(texto) == "" or _epub_valido(_limpar_caminho(texto)),
        invalid_message="Informe um arquivo .epub que exista",
    ).execute())
    return caminho or None


def _seletor_de_arquivo():
    try:
        import tkinter
        from tkinter import filedialog

        raiz = tkinter.Tk()
        raiz.withdraw()
        raiz.attributes("-topmost", True)
        try:
            caminho = filedialog.askopenfilename(
                parent=raiz,
                title="Escolha o arquivo .epub",
                initialdir=os.path.join(os.path.expanduser("~"), "Downloads"),
                filetypes=[("Livros EPUB", "*.epub"), ("Todos os arquivos", "*.*")],
            )
        finally:
            raiz.destroy()
    except Exception:
        return None
    caminho = os.path.normpath(caminho) if caminho else ""
    return caminho if _epub_valido(caminho) else None


def _limpar_caminho(texto):
    texto = texto.strip()
    if texto.startswith("& "):
        texto = texto[2:].strip()
    return texto.strip("'\"").strip()


def _epub_valido(caminho):
    return bool(caminho) and caminho.lower().endswith(".epub") and os.path.isfile(caminho)


def _escolher_biblioteca(config, mensagem):
    try:
        bibliotecas = listar_bibliotecas(config)
    except requests.RequestException as exc:
        print(f"[ERRO] Nao foi possivel listar as bibliotecas do Audiobookshelf: {exc}")
        print("Confira o endereco e o token em Configs ABS.")
        inquirer.confirm(message="Voltar", default=True).execute()
        return None
    if not bibliotecas:
        print("[ERRO] O Audiobookshelf nao tem nenhuma biblioteca.")
        inquirer.confirm(message="Voltar", default=True).execute()
        return None

    padrao = next((b for b in bibliotecas if b["nome"] == config.get("abs_biblioteca")), bibliotecas[0])
    escolhida = inquirer.select(
        message=mensagem,
        choices=[{"name": f"{b['nome']}  (pasta: {', '.join(b['pastas']) or '?'})", "value": b} for b in bibliotecas]
        + [{"name": "Cancelar", "value": None}],
        default=padrao,
        cycle=True,
    ).execute()
    if escolhida is None:
        return None
    if not escolhida["pastas"]:
        print("[ERRO] Essa biblioteca nao tem pasta configurada no Audiobookshelf.")
        inquirer.confirm(message="Voltar", default=True).execute()
        return None
    pasta = escolhida["pastas"][0]
    if len(escolhida["pastas"]) > 1:
        pasta = inquirer.select(message="Pasta da biblioteca", choices=escolhida["pastas"]).execute()

    outras = bibliotecas_na_mesma_pasta(bibliotecas, escolhida, pasta)
    if outras:
        print(f"\n[AVISO] A pasta {pasta} tambem e usada pela(s) biblioteca(s): {', '.join(outras)}.")
        print("Tudo o que for enviado aparece nas duas. Para separar, cada biblioteca precisa de uma pasta propria.")
        if not inquirer.confirm(message="Continuar mesmo assim?", default=False).execute():
            return None

    alteracoes = {"abs_biblioteca": escolhida["nome"]}
    if modo_remoto(config):
        no_servidor = pasta_no_servidor(pasta, detectar_mapeamento(config))
        if no_servidor is None:
            print(f"Nao consegui descobrir automaticamente onde fica {pasta} no servidor.")
            no_servidor = inquirer.text(
                message=f"Caminho no servidor que corresponde a {pasta}:", default=config.get("abs_remote_dir", "")
            ).execute().strip()
            if not no_servidor:
                return None
        alteracoes["abs_remote_dir"] = no_servidor
    return alteracoes


def menu_config_abs():
    while True:
        config = carregar_config()
        _clear_screen()
        print("CONFIGS DO AUDIOBOOKSHELF")
        print("=" * 50)
        print(f"Endereco (URL):       {config['abs_url']}")
        print(f"Token de API:         {_mascarar(config.get('abs_token', ''))}")
        print(f"Acesso SSH:           {config.get('abs_ssh') or '(sem SSH: grava na pasta local abs_dir)'}")
        if modo_remoto(config):
            print(f"Pasta no servidor:    {config['abs_remote_dir']}")
        else:
            print(f"Pasta local:          {config.get('abs_dir') or '(nao definida)'}")
        print(f"Biblioteca padrao:    {config.get('abs_biblioteca') or '(nao definida)'}  (usada pela CentralNovel)")
        print("=" * 50)
        escolha = inquirer.select(
            message="O que alterar?",
            choices=[
                {"name": "Biblioteca padrao (lista as bibliotecas do servidor)", "value": "biblioteca"},
                {"name": "Endereco do servidor (URL)", "value": "url"},
                {"name": "Token de API", "value": "token"},
                {"name": "Acesso SSH (usuario@host)", "value": "ssh"},
                {"name": "Testar conexao", "value": "testar"},
                {"name": "Voltar", "value": "voltar"},
            ],
            cycle=True,
        ).execute()
        if escolha == "voltar":
            return
        if escolha == "biblioteca":
            alteracoes = _escolher_biblioteca(config, "Biblioteca padrao")
            if alteracoes:
                salvar_config(alteracoes)
                print("Salvo.")
        elif escolha == "url":
            url = inquirer.text(
                message="Endereco do Audiobookshelf (ex.: http://192.168.15.3:13378):", default=config["abs_url"],
                validate=lambda t: t.strip().startswith(("http://", "https://")),
                invalid_message="Comece com http:// ou https://",
            ).execute().strip().rstrip("/")
            if _testar_api(config, url=url) or inquirer.confirm(message="Nao respondeu. Salvar mesmo assim?", default=False).execute():
                salvar_config({"abs_url": url})
                print("Salvo.")
        elif escolha == "token":
            token = inquirer.secret(message="Novo token (ABS > Configuracoes > Usuarios > seu usuario > API token):").execute().strip()
            if token and (_testar_api(config, token=token)
                          or inquirer.confirm(message="Token recusado. Salvar mesmo assim?", default=False).execute()):
                salvar_config({"abs_token": token})
                print("Salvo.")
        elif escolha == "ssh":
            ssh = inquirer.text(message="Acesso SSH (usuario@host; vazio = gravar em pasta local):",
                                default=config.get("abs_ssh", "")).execute().strip()
            teste = dict(config, abs_ssh=ssh)
            erro = verificar_destino(teste) if ssh else None
            if erro:
                print(f"[AVISO] {erro}")
            if not erro or inquirer.confirm(message="Salvar mesmo assim?", default=False).execute():
                salvar_config({"abs_ssh": ssh})
                print("Salvo.")
        elif escolha == "testar":
            _testar_tudo(config)
        inquirer.confirm(message="Continuar", default=True).execute()


def _mascarar(token):
    return f"definido (termina em ...{token[-4:]})" if token else "(nao definido)"


def _testar_api(config, url=None, token=None):
    try:
        bibliotecas = listar_bibliotecas(config, url=url, token=token)
    except requests.RequestException as exc:
        print(f"[FALHA] API do Audiobookshelf: {exc}")
        return False
    print(f"[OK] API respondeu: {len(bibliotecas)} biblioteca(s): {', '.join(b['nome'] for b in bibliotecas)}")
    return True


def _testar_tudo(config):
    _testar_api(config)
    if not modo_remoto(config):
        erro = verificar_destino(config)
        print(f"[{'FALHA' if erro else 'OK'}] Pasta local: {erro or config['abs_dir']}")
        return
    erro = verificar_destino(config)
    print(f"[{'FALHA' if erro else 'OK'}] SSH e pasta no servidor: {erro or config['abs_remote_dir']}")
    mapa = detectar_mapeamento(config)
    if mapa:
        print("[OK] Pastas do container detectadas:")
        for dentro, fora in sorted(mapa.items()):
            print(f"       {dentro}  ->  {fora}")
    else:
        print("[AVISO] Nao consegui ler as pastas do container (docker inspect via SSH).")


def menu_conversao():
    while True:
        _clear_screen()
        escolha = inquirer.select(
            message="MENU CONVERSAO",
            choices=[
                {"name": "Converter arquivo PDF", "value": "arquivo"},
                {"name": "Converter pasta", "value": "pasta"},
                {"name": "Converter tudo (com subpastas)", "value": "pasta_rec"},
                {"name": "Limpar PDFs (remover link e marca de traducao)", "value": "limpar"},
                {"name": "Configuracoes", "value": "config"},
                {"name": "Voltar", "value": "voltar"},
            ],
            cycle=True,
        ).execute()

        if escolha == "voltar":
            return

        if escolha == "config":
            _clear_screen()
            print("CONFIGURACOES")
            print("=" * 40)
            print(f"Qualidade JPG: {QUALIDADE_JPG}")
            print(f"DPI: {DPI}")
            inquirer.confirm(message="Voltar", default=True).execute()
            continue

        if escolha == "arquivo":
            _converter_arquivo()
            continue

        if escolha == "limpar":
            _limpar_pdfs()
            continue

        if escolha == "pasta":
            _converter_pasta(recursive=False)
            continue

        if escolha == "pasta_rec":
            _converter_pasta(recursive=True)


def _resolver_ja_baixados(capitulos, novel_title, saidas, audio_prontos):
    existentes, faltantes = separar_baixados(capitulos, novel_title, saidas, audio_prontos)
    if not existentes:
        return capitulos, False

    _clear_screen()
    print(f"{len(existentes)} de {len(capitulos)} capitulos ja possuem as saidas escolhidas:")
    print(f"  {resumir_capitulos(existentes)}")
    if faltantes:
        print(f"Faltam {len(faltantes)}: {resumir_capitulos(faltantes)}")
    print()

    choices = []
    if faltantes:
        choices.append(
            {"name": f"Processar apenas os faltantes ({len(faltantes)})", "value": (faltantes, False)}
        )
    choices.append(
        {"name": f"Reescrever os ja existentes ({len(existentes)})", "value": (existentes, True)}
    )
    if faltantes:
        choices.append(
            {"name": f"Ambos: faltantes + reescrever ({len(capitulos)})", "value": (capitulos, True)}
        )
    choices.append({"name": "Cancelar", "value": None})

    return inquirer.select(
        message="Alguns capitulos ja existem. O que fazer?",
        choices=choices,
        cycle=True,
    ).execute()


def _tratar_falhas(falhas, novel_title, saidas, sobrescrever, criar_sessao=None):
    caminho_log = None
    while falhas:
        caminho_log = gravar_log_falhas(falhas, novel_title, caminho_log)
        print(f"\n{len(falhas)} capitulo(s) com erro: {resumir_capitulos([f['cap'] for f in falhas])}")
        print(f"Log: {caminho_log}")
        if not inquirer.confirm(message="Tentar novamente os que falharam?", default=True).execute():
            return
        resultado = download_capitulos_novel(
            [item["cap"] for item in falhas],
            novel_title,
            saidas=saidas,
            sobrescrever=sobrescrever,
            sessao_audio=criar_sessao() if criar_sessao else None,
        )
        falhas = resultado["falhas"]

    if caminho_log:
        os.remove(caminho_log)
        print("\nTodos os capitulos com erro foram recuperados (log removido).")


def _selecionar_novel():
    top_novels = listar_top_novels(limite=10)
    choices = [{"name": f"{index}. {item['title']}", "value": item} for index, item in enumerate(top_novels, 1)]
    choices.extend(
        [
            {"name": "Buscar por nome", "value": "buscar"},
            {"name": "Informar link da novel", "value": "link"},
            {"name": "Voltar", "value": None},
        ]
    )

    escolha = inquirer.select(
        message="Escolha uma novel",
        choices=choices,
        cycle=True,
        height=min(20, len(choices) + 2),
    ).execute()

    if escolha is None:
        return None
    if isinstance(escolha, dict):
        return escolha
    if escolha == "link":
        return _selecionar_novel_por_link()
    return _selecionar_novel_por_busca()


def _selecionar_novel_por_link():
    while True:
        entrada = inquirer.text(message="Cole o link da novel (ou vazio para voltar):").execute().strip()
        if not entrada:
            return None
        url = normalizar_url_novel(entrada)
        if not url:
            if not inquirer.confirm(message="Link invalido. Tentar novamente?", default=True).execute():
                return None
            continue
        return {"title": obter_titulo_novel(url), "url": url}


def _selecionar_novel_por_busca():
    termo = inquirer.text(message="Digite o nome da novel (ou vazio para voltar):").execute().strip()
    if not termo:
        return None

    resultados = buscar_novels_por_nome(termo, limite=20)
    if not resultados:
        inquirer.confirm(message="Nenhuma novel encontrada. Voltar?", default=True).execute()
        return None

    escolha = inquirer.select(
        message="Resultados da busca",
        choices=[{"name": item["title"], "value": item} for item in resultados]
        + [{"name": "Voltar", "value": None}],
        cycle=True,
        height=min(20, len(resultados) + 3),
    ).execute()
    return escolha


def _selecionar_capitulos_ou_volumes(capitulos):
    modo = inquirer.select(
        message="Selecione o tipo de download",
        choices=[
            {"name": "Capitulos especificos", "value": "caps"},
            {"name": "Volumes completos", "value": "vols"},
            {"name": "Voltar (escolher outra novel)", "value": "voltar"},
        ],
        cycle=True,
    ).execute()

    if modo == "voltar":
        return None
    if modo == "caps":
        return _selecionar_capitulos(capitulos)
    return _selecionar_volumes(capitulos)


def _selecionar_capitulos(capitulos):
    cap_nums = sorted({int(item["capitulo"]) for item in capitulos})
    entrada = inquirer.text(
        message=(
            f"Digite os capitulos ({cap_nums[0]} a {cap_nums[-1]}), "
            "ex: 1,2,10-15. Vazio cancela:"
        )
    ).execute().strip()
    if not entrada:
        return []

    try:
        selecionados = set(parse_numero_lista_ou_intervalo(entrada))
    except ValueError:
        inquirer.confirm(message="Formato invalido. Voltar?", default=True).execute()
        return []
    return [item for item in capitulos if int(item["capitulo"]) in selecionados]


def _selecionar_volumes(capitulos):
    volumes_disponiveis = _listar_volumes(capitulos)
    selecionados = _checkbox_com_todos(
        "Selecione um ou mais volumes",
        [{"name": volume, "value": volume} for volume in volumes_disponiveis],
        f"[ Todos os {len(volumes_disponiveis)} volumes ]",
        altura_max=20,
    )
    if not selecionados:
        return []
    volumes_set = set(selecionados)
    return [item for item in capitulos if item["volume"] in volumes_set]


_TODOS = "__todos__"


def _checkbox_com_todos(mensagem, opcoes, rotulo_todos, altura_max=25, **extras):
    escolhidos = inquirer.checkbox(
        message=f"{mensagem} (espaco marca, ctrl+a marca todos, ctrl+r inverte, enter confirma)",
        choices=[{"name": rotulo_todos, "value": _TODOS}] + opcoes,
        cycle=True,
        height=min(altura_max, len(opcoes) + 3),
        **extras,
    ).execute()
    if any(isinstance(item, str) and item == _TODOS for item in escolhidos):
        return [opcao["value"] for opcao in opcoes]
    return escolhidos


def _perguntar_saidas(anteriores=None):
    padrao = set(anteriores) if anteriores else {"pdf"}
    opcoes = [("pdf", "PDF"), ("cbz", "CBZ"), ("audio", "Audiolivro (Audiobookshelf)")]
    return set(
        inquirer.checkbox(
            message="O que gerar? (espaco marca, enter confirma)",
            choices=[
                {"name": nome, "value": valor, "enabled": valor in padrao}
                for valor, nome in opcoes
            ],
            validate=lambda escolhidos: len(escolhidos) > 0,
            invalid_message="Selecione pelo menos uma opcao",
            cycle=True,
        ).execute()
    )


def _converter_arquivo():
    _clear_screen()
    pdf_path = inquirer.text(message="Caminho do PDF (vazio cancela):").execute().strip().strip('"')
    if not pdf_path:
        return
    output_folder = inquirer.text(message="Pasta de saida (vazio = mesma pasta):").execute().strip().strip('"')
    output_folder = output_folder if output_folder else None
    resultado = converter_pdf_para_cbz(pdf_path, output_folder)
    _clear_screen()
    print(f"[SUCESSO] {resultado}" if resultado else "[ERRO] Falhou")
    inquirer.confirm(message="Voltar", default=True).execute()


def _converter_pasta(recursive):
    _clear_screen()
    pasta_path = inquirer.text(message="Caminho da pasta (vazio cancela):").execute().strip().strip('"')
    if not pasta_path:
        return
    output_folder = inquirer.text(message="Pasta de saida (vazio = mesma pasta):").execute().strip().strip('"')
    output_folder = output_folder if output_folder else None
    sucessos, falhas = processar_pasta(pasta_path, output_folder, recursive=recursive)
    _clear_screen()
    _imprimir_resultado_conversao(sucessos, falhas)
    inquirer.confirm(message="Voltar", default=True).execute()


def _limpar_pdfs():
    _clear_screen()
    pasta = inquirer.text(message="Pasta dos PDFs (inclui subpastas) [PDF]:", default="PDF").execute().strip().strip('"')
    if not pasta:
        return
    if not os.path.isdir(pasta):
        print(f"[ERRO] Pasta nao encontrada: {pasta}")
    else:
        alterados, falhas, total = limpar_pasta(pasta)
        print(f"\nPDFs:{total} | Alterados: {alterados} | Falhas: {falhas}")
    inquirer.confirm(message="Voltar", default=True).execute()


def _listar_volumes(capitulos):
    return sorted({item["volume"] for item in capitulos}, key=_ordenar_volume)


def _ordenar_volume(value):
    texto = str(value).strip()
    if texto.isdigit():
        return (0, int(texto))
    return (1, _normalizar_volume(texto))


def _normalizar_volume(value):
    return " ".join(str(value).strip().lower().split())


def _clear_screen():
    os.system("cls" if os.name == "nt" else "clear")


def _imprimir_resultado_conversao(sucessos, falhas):
    print(f"\n{'=' * 60}")
    print("RESULTADO")
    print(f"{'=' * 60}")
    print(f"Sucessos: {sucessos}")
    print(f"Falhas: {falhas}")
    print(f"{'=' * 60}")

