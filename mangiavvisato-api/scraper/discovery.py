"""Scoperta degli URL dei richiami: feed RSS, pagina elenco e, per lo storico,
tutte le pagine dell'elenco."""

from __future__ import annotations

import logging
import re
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
        requests_before = len(getattr(fetcher, "data_requests", []))
        html = fetcher.get(url).text
        all_links = urls_from_listing(html)
        new = [u for u in all_links if u not in known]
        nxt = next_page_url(html, url)
        log.info(
            "Pagina elenco %d (%s): %d link a richiami, %d nuovi; pagina successiva: %s",
            page, url, len(all_links), len(new), nxt or f"non trovata, provo {with_page_param(start_url, page + 1)}",
        )
        if page < 3:
            _log_diagnostics(fetcher, html, url, requests_before)
            if hasattr(fetcher, "save_snapshot"):
                fetcher.save_snapshot(f"elenco-pagina-{page}", html)
        if not new:
            break
        found += new
        known.update(new)
        page += 1
        url = nxt or with_page_param(start_url, page)
    return found


def _log_diagnostics(fetcher, html: str, url: str, requests_before: int) -> None:
    """Scrive nel log gli indizi utili per capire come è paginato l'elenco."""
    soup = BeautifulSoup(html, "html.parser")
    pager = []
    for a in soup.find_all("a", href=True):
        text = a.get_text(" ", strip=True)
        href = a["href"]
        if re.search(r"page|pagin|offset|start=", href, re.I) or re.fullmatch(r"\d{1,4}|›|»|successiv\w*|next", text, re.I):
            pager.append(f"[{text}] {href}")
    log.info("  link di paginazione trovati: %s", pager[:20] or "nessuno")
    forms = [f"{f.get('method', 'get')} {f.get('action', '')}" for f in soup.find_all("form")]
    log.info("  moduli nella pagina: %s", forms[:10] or "nessuno")
    reqs = getattr(fetcher, "data_requests", [])[requests_before:]
    log.info("  richieste dati fatte dalla pagina: %d", len(reqs))
    for r in reqs[:30]:
        log.info("    %s", r)


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
