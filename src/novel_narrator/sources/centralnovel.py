import re
import time
import xml.etree.ElementTree as ET
from difflib import SequenceMatcher
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from novel_narrator.models import Chapter, Novel
from novel_narrator.sources.base import Source, SourceError

SITE_URL = "https://centralnovel.com/"
AJAX_URL = SITE_URL + "wp-admin/admin-ajax.php"
POPULAR_URL = SITE_URL + "series/?status=&order=popular"
SITEMAP_URL = SITE_URL + "series-sitemap.xml"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": SITE_URL,
}

_RE_SITE_URL = re.compile(r"https?://(?:www\.)?centralnovel\.com\S*", re.IGNORECASE)
_RE_TRANSLATION_MARK = re.compile(
    r"(traduzid[oa]|tradu[cç][aã]o|translated)\b.*\b"
    r"(chatgpt|gpt|deepl|gemini|claude|copilot|google|tradutor|"
    r"intelig[eê]ncia artificial|ia|usando|using|automatic\w*|machine)\b",
    re.IGNORECASE | re.DOTALL,
)
_MAX_MARK_LENGTH = 150


class CentralNovel(Source):
    name = "Central Novel"

    def __init__(self, session=None, retries=3, retry_wait_s=2):
        self.session = session or requests.Session()
        self.session.headers.update(HEADERS)
        self.retries = retries
        self.retry_wait_s = retry_wait_s
        self._sitemap = None

    def popular(self, limit=10):
        return parse_popular(self._get(POPULAR_URL))[:limit]

    def search(self, query, limit=10):
        if self._sitemap is None:
            self._sitemap = parse_sitemap(self._get(SITEMAP_URL))
        return rank_by_title(self._sitemap, query)[:limit]

    def parse_url(self, text):
        return normalize_novel_url(text)

    def novel(self, url):
        return parse_novel_page(self._get(url), url)

    def chapters(self, novel):
        return parse_chapter_list(self._get(novel.url))

    def chapter_text(self, chapter):
        last_error = None
        for attempt in range(1, self.retries + 1):
            try:
                paragraphs = parse_paragraphs(self._get(chapter.url))
                if paragraphs:
                    return paragraphs
                last_error = "texto do capitulo nao encontrado na pagina"
            except SourceError as exc:
                last_error = str(exc)
            if attempt < self.retries:
                time.sleep(self.retry_wait_s * attempt)
        raise SourceError(f"{chapter.url}: {last_error}")

    def cover(self, novel):
        if not novel.cover_url:
            return None
        try:
            response = self.session.get(novel.cover_url, timeout=30)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise SourceError(f"{novel.cover_url}: {exc}") from exc
        return response.content, "png" if novel.cover_url.lower().endswith(".png") else "jpg"

    def pdf_url(self, chapter):
        pdf_page = pdf_page_url(chapter)
        post_id = chapter.source_id or _post_id(self._get(pdf_page))
        if not post_id:
            raise SourceError(f"{pdf_page}: post_id nao encontrado")
        try:
            response = self.session.post(
                AJAX_URL,
                data={"action": "ts_ln_dl_url", "post_id": post_id},
                headers={"Referer": pdf_page, "Content-Type": "application/x-www-form-urlencoded"},
                timeout=30,
            )
            response.raise_for_status()
            result = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise SourceError(f"token do PDF: {exc}") from exc
        if result.get("error") == 0 and result.get("url"):
            return result["url"].replace("\\/", "/")
        raise SourceError(f"token do PDF: resposta inesperada {result}")

    def _get(self, url):
        try:
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise SourceError(f"{url}: {exc}") from exc
        response.encoding = "utf-8"
        return response.text


def pdf_page_url(chapter):
    return chapter.url.rstrip("/") + "/pdf/"


def parse_popular(html):
    soup = BeautifulSoup(html, "html.parser")
    novels = []
    for article in soup.select("article"):
        anchor = article.select_one("a[href*='/series/']")
        if not anchor:
            continue
        url = anchor.get("href", "").strip()
        if not _is_novel_url(url):
            continue
        title = anchor.get("title", "").strip() or " ".join(article.get_text(" ", strip=True).split())
        image = article.select_one("img")
        cover = (image.get("src") or image.get("data-src") or "").strip() if image else ""
        novels.append(Novel(title=title or _title_from_url(url), url=_clean_url(url), cover_url=cover))
    return _dedupe(novels)


def parse_sitemap(xml):
    root = ET.fromstring(xml)
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    urls = [i.text.strip() for i in root.findall(".//sm:url/sm:loc", ns) if i.text and _is_novel_url(i.text)]
    return _dedupe([Novel(title=_title_from_url(u), url=_clean_url(u)) for u in urls])


def rank_by_title(novels, query):
    query = _simplify(query)
    if not query:
        return []
    scored = []
    for novel in novels:
        title = _simplify(novel.title)
        if not title:
            continue
        if query == title:
            score = 100.0
        elif query in title:
            score = 90.0
        else:
            parts = [p for p in query.split() if len(p) > 1]
            if parts and all(p in title for p in parts):
                score = 80.0
            else:
                score = SequenceMatcher(None, query, title).ratio() * 100
        if score >= 45:
            scored.append((score, novel))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [novel for _, novel in scored]


def parse_novel_page(html, url):
    soup = BeautifulSoup(html, "html.parser")
    title = ""
    for selector in ("h1.entry-title", ".entry-title", "h1[itemprop='headline']"):
        node = soup.select_one(selector)
        if node and node.get_text(strip=True):
            title = node.get_text(" ", strip=True)
            break
    author = ""
    for span in soup.select(".spe span"):
        text = span.get_text(" ", strip=True)
        if text.lower().startswith("autor"):
            author = text.split(":", 1)[-1].strip()
            break
    image = soup.select_one(".thumb img, .thumbook img")
    cover = image["src"].split("?")[0] if image and image.get("src") else ""
    return Novel(title=title or _title_from_url(url), url=_clean_url(url), author=author, cover_url=cover)


def parse_chapter_list(html):
    soup = BeautifulSoup(html, "html.parser")
    chapters = []
    for item in soup.find_all("li", {"data-id": True}):
        number_node = item.find("div", class_="epl-num")
        link = item.find("a", href=True)
        if not number_node or not link:
            continue
        volume, number = _volume_and_number(number_node.get_text(" ", strip=True))
        if not number:
            continue
        title_node = item.find("div", class_="epl-title")
        date_node = item.find("div", class_="epl-date")
        chapters.append(
            Chapter(
                number=int(number),
                title=title_node.get_text(strip=True) if title_node else "Sem titulo",
                volume=volume,
                url=_chapter_page_url(link["href"]),
                date=date_node.get_text(strip=True) if date_node else "",
                source_id=item["data-id"],
            )
        )
    chapters.sort(key=_chapter_order)
    return chapters


def parse_paragraphs(html):
    soup = BeautifulSoup(html, "html.parser")
    content = soup.select_one("div.epcontent")
    if content is None:
        return []
    for line_break in content.find_all("br"):
        line_break.replace_with("\n")
    paragraphs = []
    for tag in content.find_all("p"):
        for line in tag.get_text().split("\n"):
            line = " ".join(line.split())
            if line and not _is_noise(line):
                paragraphs.append(line)
    return paragraphs


def normalize_novel_url(text):
    text = text.strip().strip("\"'")
    if text.startswith(("http://", "https://")):
        url = text
    elif text.startswith("/series/"):
        url = SITE_URL.rstrip("/") + text
    elif "/series/" in text:
        url = "https://" + text
    else:
        return None
    if "centralnovel.com" not in urlparse(url).netloc or not _is_novel_url(url):
        return None
    return _clean_url(url)


def _chapter_page_url(href):
    url = _clean_url(href).rstrip("/")
    if url.endswith("/pdf"):
        url = url[: -len("/pdf")]
    return url + "/"


def _post_id(html):
    match = re.search(r'"post_id":\s*(\d+)', html) or re.search(r'data-id["\s]*[:=]["\s]*(\d+)', html)
    return match.group(1) if match else None


def _volume_and_number(text):
    match = re.search(r"Vol\.\s*(.*?)\s*Cap\.\s*(\d+)", text, flags=re.IGNORECASE)
    if match:
        return " ".join(match.group(1).split()) or "1", match.group(2)
    match = re.search(r"Cap\.\s*(\d+)", text, flags=re.IGNORECASE)
    if match:
        return "1", match.group(1)
    numbers = re.findall(r"\d+", text)
    if len(numbers) >= 2:
        return numbers[0], numbers[1]
    if len(numbers) == 1:
        return "1", numbers[0]
    return None, None


def _chapter_order(chapter):
    if chapter.volume.isdigit():
        return (0, int(chapter.volume), chapter.number)
    return (1, _simplify(chapter.volume), chapter.number)


def has_site_link(text):
    return bool(_RE_SITE_URL.search(text))


def is_translation_mark(text):
    text = " ".join(text.split())
    return len(text) <= _MAX_MARK_LENGTH and bool(_RE_TRANSLATION_MARK.search(text))


def _is_noise(text):
    return has_site_link(text) or is_translation_mark(text)


def _dedupe(novels):
    seen = set()
    unique = []
    for novel in novels:
        if novel.url not in seen:
            seen.add(novel.url)
            unique.append(novel)
    return unique


def _is_novel_url(url):
    url = _clean_url(url or "")
    return "/series/" in url and not url.rstrip("/").endswith("/series") and "/series/list-mode" not in url


def _clean_url(url):
    return url.split("#")[0].split("?")[0].strip()


def _title_from_url(url):
    path = urlparse(url).path.strip("/")
    slug = re.sub(r"-\d{8}$", "", path.split("/")[-1] if path else "")
    return " ".join(part.capitalize() for part in slug.split("-") if part)


def _simplify(text):
    text = re.sub(r"[^a-z0-9\s]", " ", text.lower().strip())
    return " ".join(text.split())
