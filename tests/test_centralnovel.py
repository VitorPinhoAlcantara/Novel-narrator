from pathlib import Path

import pytest

from novel_narrator.models import Chapter, Novel
from novel_narrator.sources.base import SourceError
from novel_narrator.sources.centralnovel import (
    CentralNovel,
    normalize_novel_url,
    parse_chapter_list,
    parse_novel_page,
    parse_paragraphs,
    parse_popular,
    parse_sitemap,
    pdf_page_url,
    rank_by_title,
)

FIXTURES = Path(__file__).parent / "fixtures"
NOVEL_URL = "https://centralnovel.com/series/novel-teste-20260913/"


def fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_novel_page_has_title_author_and_clean_cover():
    novel = parse_novel_page(fixture("centralnovel_novel.html"), NOVEL_URL + "?x=1")
    assert novel == Novel(
        title="Novel Teste",
        url=NOVEL_URL,
        author="Fulano de Tal",
        cover_url="https://centralnovel.com/wp-content/uploads/capa-teste.png",
    )


def test_chapter_list_skips_template_and_sorts_by_volume():
    chapters = parse_chapter_list(fixture("centralnovel_novel.html"))
    assert [(c.volume, c.number) for c in chapters] == [("1", 1), ("1", 2), ("2", 12), ("Extra", 0)]
    assert chapters[2] == Chapter(
        number=12,
        title="Segundo Volume",
        volume="2",
        url="https://centralnovel.com/novel-teste-capitulo-12/",
        date="outubro 27, 2026",
        source_id="302",
    )


def test_paragraphs_drop_translation_mark_and_site_links():
    paragraphs = parse_paragraphs(fixture("centralnovel_chapter.html"))
    assert paragraphs[:3] == ["Primeiro paragrafo do capitulo.", "Linha de cima", "Linha de baixo"]
    assert len(paragraphs) == 4
    assert paragraphs[3].startswith("A traducao do texto antigo usando metodos")


def test_page_without_content_has_no_paragraphs():
    assert parse_paragraphs("<html><body><p>nada</p></body></html>") == []


def test_popular_keeps_only_unique_novel_links():
    novels = parse_popular(fixture("centralnovel_popular.html"))
    assert [(n.title, n.url) for n in novels] == [
        ("Novel Teste", NOVEL_URL),
        ("Outra Novel", "https://centralnovel.com/series/outra-novel/"),
    ]


def test_sitemap_titles_come_from_slug():
    xml = (
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        "<url><loc>https://centralnovel.com/series/shadow-slave-20260913/</loc></url>"
        "<url><loc>https://centralnovel.com/series/</loc></url>"
        "</urlset>"
    )
    assert parse_sitemap(xml) == [
        Novel(title="Shadow Slave", url="https://centralnovel.com/series/shadow-slave-20260913/")
    ]


def test_search_ranks_exact_and_partial_matches():
    novels = [Novel("Lord of Mysteries"), Novel("Shadow Slave"), Novel("Slave Harem"), Novel("Supreme Magus")]
    assert [n.title for n in rank_by_title(novels, "shadow slave")] == ["Shadow Slave"]
    assert [n.title for n in rank_by_title(novels, "slave")][:2] == ["Shadow Slave", "Slave Harem"]
    assert rank_by_title(novels, "  ") == []


def test_normalize_novel_url():
    assert normalize_novel_url("centralnovel.com/series/novel-teste-20260913/") == NOVEL_URL
    assert normalize_novel_url("/series/novel-teste-20260913/") == NOVEL_URL
    assert normalize_novel_url(f'"{NOVEL_URL}#capitulos"') == NOVEL_URL
    assert normalize_novel_url("https://outrosite.com/series/x/") is None
    assert normalize_novel_url("shadow slave") is None


def test_pdf_page_url():
    assert pdf_page_url(Chapter(1, "x", url="https://centralnovel.com/a-capitulo-1/")) == (
        "https://centralnovel.com/a-capitulo-1/pdf/"
    )


def test_chapter_text_retries_then_raises(monkeypatch):
    source = CentralNovel(retries=2, retry_wait_s=0)
    calls = []

    def fail(url):
        calls.append(url)
        raise SourceError("503")

    monkeypatch.setattr(source, "_get", fail)
    with pytest.raises(SourceError, match="503"):
        source.chapter_text(Chapter(1, "x", url="https://centralnovel.com/a/"))
    assert len(calls) == 2
