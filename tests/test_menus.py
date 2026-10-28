from unittest import mock

from centralnovel import menus

NOVEL = {"title": "Shadow Slave", "url": "https://exemplo/series/shadow-slave/"}
OUTRA = {"title": "Outra Novel", "url": "https://exemplo/series/outra/"}
CAP = {"volume": "1", "capitulo": "1", "titulo": "A", "url": "u"}


def _confirm(respostas):
    fila = iter(respostas)

    def fabrica(*args, **kwargs):
        return mock.Mock(execute=mock.Mock(return_value=next(fila)))

    return fabrica


def _rodar(novels, selecoes, respostas_confirm=()):
    with mock.patch.object(menus, "_clear_screen"), \
            mock.patch.object(menus, "_selecionar_novel", side_effect=novels) as novel, \
            mock.patch.object(menus, "extrair_links_pdf", return_value=[CAP]), \
            mock.patch.object(menus, "_selecionar_capitulos_ou_volumes", side_effect=selecoes) as caps, \
            mock.patch.object(menus, "_perguntar_saidas", return_value={"cbz"}) as saidas, \
            mock.patch.object(menus, "_resolver_ja_baixados", return_value=([CAP], False)), \
            mock.patch.object(menus, "download_capitulos_novel", return_value={"sucessos": 1, "falhas": []}) as baixar, \
            mock.patch.object(menus, "_tratar_falhas"), \
            mock.patch.object(menus.inquirer, "confirm", side_effect=_confirm(respostas_confirm)):
        menus.menu_download()
    return novel, caps, saidas, baixar


def test_apos_processar_volta_direto_para_selecao_de_capitulos():
    novel, caps, saidas, baixar = _rodar(
        novels=[NOVEL, None], selecoes=[[CAP], [CAP], None], respostas_confirm=[True, True]
    )
    assert novel.call_count == 2
    assert caps.call_count == 3
    assert baixar.call_count == 2
    assert saidas.call_args_list[1].args[0] == {"cbz"}


def test_voltar_na_selecao_permite_escolher_outra_novel():
    novel, caps, saidas, baixar = _rodar(novels=[NOVEL, OUTRA, None], selecoes=[None, [CAP], None],
                                         respostas_confirm=[True])
    assert novel.call_count == 3
    assert baixar.call_count == 1


def test_nenhuma_novel_volta_ao_menu_principal_sem_baixar():
    novel, caps, saidas, baixar = _rodar(novels=[None], selecoes=[])
    assert baixar.call_count == 0


def test_menu_principal_nao_tem_atalho_de_audiolivro():
    assert not hasattr(menus, "menu_audiolivro")


def test_menu_principal_tem_as_quatro_opcoes():
    capturado = {}

    def falso_select(**kwargs):
        capturado["opcoes"] = [c["name"] for c in kwargs["choices"]]
        return mock.Mock(execute=mock.Mock(return_value="sair"))

    with mock.patch.object(menus, "_clear_screen"), mock.patch.object(menus.inquirer, "select", side_effect=falso_select):
        menus.menu_principal()
    nomes = capturado["opcoes"]
    assert len(nomes) == 4
    assert nomes[0].startswith("CentralNovel") and nomes[1].startswith("EPUB local")
    assert nomes[2].startswith("Configs ABS") and nomes[3] == "Sair"


def test_limpar_caminho_aceita_o_que_o_terminal_cola():
    assert menus._limpar_caminho(r'  "C:\Livros\O Livro da Lua.epub"  ') == r"C:\Livros\O Livro da Lua.epub"
    assert menus._limpar_caminho(r"'C:\Livros\O Livro da Lua.epub'") == r"C:\Livros\O Livro da Lua.epub"
    assert menus._limpar_caminho(r"& 'C:\Livros\a b.epub'") == r"C:\Livros\a b.epub"
    assert menus._limpar_caminho("   ") == ""


def test_seletor_do_windows_tem_prioridade(tmp_path):
    epub = tmp_path / "livro.epub"
    epub.write_bytes(b"x")
    with mock.patch.object(menus, "_seletor_de_arquivo", return_value=str(epub)), \
            mock.patch.object(menus.inquirer, "filepath") as campo:
        assert menus._perguntar_epub() == str(epub)
    campo.assert_not_called()


def test_sem_escolha_na_janela_cai_no_campo_de_arrastar(tmp_path):
    epub = tmp_path / "livro com espaco.epub"
    epub.write_bytes(b"x")
    resposta = mock.Mock(execute=mock.Mock(return_value=f'"{epub}"'))
    with mock.patch.object(menus, "_seletor_de_arquivo", return_value=None), \
            mock.patch.object(menus.inquirer, "filepath", return_value=resposta):
        assert menus._perguntar_epub() == str(epub)


def test_campo_vazio_volta_ao_menu():
    resposta = mock.Mock(execute=mock.Mock(return_value=""))
    with mock.patch.object(menus, "_seletor_de_arquivo", return_value=None), \
            mock.patch.object(menus.inquirer, "filepath", return_value=resposta):
        assert menus._perguntar_epub() is None


def test_seletor_falhando_nao_quebra(monkeypatch):
    import tkinter

    def quebrar(*args, **kwargs):
        raise tkinter.TclError("sem display")

    monkeypatch.setattr(tkinter, "Tk", quebrar)
    assert menus._seletor_de_arquivo() is None


def _checkbox_respondendo(resposta, capturado):
    def falso(**kwargs):
        capturado.update(kwargs)
        return mock.Mock(execute=mock.Mock(return_value=resposta))
    return falso


def test_opcao_todos_retorna_todos_os_itens():
    capturado = {}
    opcoes = [{"name": "Cap 1", "value": {"i": 1}}, {"name": "Cap 2", "value": {"i": 2}}]
    with mock.patch.object(menus.inquirer, "checkbox", side_effect=_checkbox_respondendo([menus._TODOS], capturado)):
        assert menus._checkbox_com_todos("Capitulos", opcoes, "[ Todos ]") == [{"i": 1}, {"i": 2}]
    assert capturado["choices"][0] == {"name": "[ Todos ]", "value": menus._TODOS}
    assert "ctrl+a marca todos" in capturado["message"]


def test_sem_todos_retorna_so_os_marcados():
    opcoes = [{"name": "1", "value": "1"}, {"name": "2", "value": "2"}, {"name": "3", "value": "3"}]
    with mock.patch.object(menus.inquirer, "checkbox", side_effect=_checkbox_respondendo(["2"], {})):
        assert menus._checkbox_com_todos("Volumes", opcoes, "[ Todos ]") == ["2"]


def test_volumes_com_todos_seleciona_todos_os_capitulos():
    caps = [{"volume": "1", "capitulo": "1"}, {"volume": "2", "capitulo": "2"}, {"volume": "3", "capitulo": "3"}]
    with mock.patch.object(menus.inquirer, "checkbox", side_effect=_checkbox_respondendo([menus._TODOS], {})):
        assert menus._selecionar_volumes(caps) == caps
