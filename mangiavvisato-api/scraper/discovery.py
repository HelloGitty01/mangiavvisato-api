"""Scoperta degli URL dei richiami: feed RSS, pagina elenco e, per lo storico,
tutte le pagine dell'elenco."""

from __future__ import annotations

import logging
from urllib.parse import parse_qsl, urlencode, urldefrag, urljoin, urlsplit, urlunsplit

import feedparser
from bs4 import BeautifulSoup

from . import config
from .fetch import Fetcher

log = logging.getLogger(__name__)

NEXT_TEXTS = {"successiva", "successivo", "pagina successiva", "avanti", "next", "›", "»", ">"}


def normalize_url(url: str) -> str:
    url = urldefrag(urljoin(config.BASE_URL, url.strip()))[0]
    return url if url.endswith("/") else url + "/"


def is_detail_url(url: str) -> bool:
    return config.DETAIL_PATH_MARKER in url


def urls_from_feed(xml_text: str) -> list[str]:
    parsed = feedparser.parse(xml_text)
    urls = []
    for entry in parsed.entries:
        link = entry.get("link")
        if link and is_detail_url(link):
            urls.append(normalize_url(link))
    return urls


def urls_from_listing(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    urls = []
    for a in soup.find_all("a", href=True):
        if is_detail_url(a["href"]):
            urls.append(normalize_url(a["href"]))
    return list(dict.fromkeys(urls))


def next_page_url(html: str, current_url: str) -> str | None:
    """Cerca il link alla pagina successiva: rel="next", classi 'next' o testo."""
    soup = BeautifulSoup(html, "html.parser")
    link = soup.find("a", rel=lambda v: v and "next" in v, href=True)
    if not link:
        link = soup.find("a", class_=lambda c: c and "next" in c.lower(), href=True)
    if not link:
        for a in soup.find_all("a", href=True):
            text = a.get_text(" ", strip=True).lower()
            label = (a.get("aria-label") or a.get("title") or "").lower()
            if text in NEXT_TEXTS or "successiv" in label or "next" in label:
                link = a
                break
    if not link:
        return None
    return urldefrag(urljoin(current_url, link["href"]))[0]


def with_page_param(url: str, page: int) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query))
    query[config.PAGE_PARAM] = str(page)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


def crawl_listing(fetcher: Fetcher, start_url: str, max_pages: int) -> list[str]:
    """Sfoglia l'elenco pagina per pagina finché non trova più richiami nuovi."""
    found: list[str] = []
    known: set[str] = set()
    visited: set[str] = set()
    url: str | None = start_url
    page = 0
    while url and page < max_pages and url not in visited:
        visited.add(url)
        html = fetcher.get(url).text
        new = [u for u in urls_from_listing(html) if u not in known]
        log.info("Pagina elenco %d: %d richiami nuovi", page, len(new))
        if not new:
            break
        found += new
        known.update(new)
        page += 1
        url = next_page_url(html, url) or with_page_param(start_url, page)
    return found


def discover(fetcher: Fetcher, backfill: bool = False) -> list[str]:
    """URL dei richiami trovati, senza duplicati, dal più recente."""
    found: list[str] = []
    for url in config.FEED_URLS:
        try:
            feed_urls = urls_from_feed(fetcher.get(url).text)
            log.info("Feed %s: %d richiami", url, len(feed_urls))
            found += feed_urls
        except Exception as exc:  # noqa: BLE001 - il feed è solo una delle fonti
            log.warning("Feed non disponibile (%s): %s", url, exc)
    for url in config.LISTING_URLS:
        try:
            if backfill:
                listed = crawl_listing(fetcher, url, config.BACKFILL_MAX_PAGES)
            else:
                listed = urls_from_listing(fetcher.get(url).text)
            log.info("Elenco %s: %d richiami", url, len(listed))
            found += listed
        except Exception as exc:  # noqa: BLE001
            log.warning("Elenco non disponibile (%s): %s", url, exc)
    return list(dict.fromkeys(found))
