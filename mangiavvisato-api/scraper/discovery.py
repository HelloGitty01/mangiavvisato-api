"""Scoperta degli URL dei richiami: feed RSS e, come riserva, pagine elenco."""

from __future__ import annotations

import logging
from urllib.parse import urljoin, urldefrag

import feedparser
from bs4 import BeautifulSoup

from . import config
from .fetch import Fetcher

log = logging.getLogger(__name__)


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
    return urls


def discover(fetcher: Fetcher) -> list[str]:
    """Restituisce gli URL dei richiami trovati, senza duplicati, in ordine di scoperta."""
    found: list[str] = []
    for url in config.FEED_URLS:
        try:
            found += urls_from_feed(fetcher.get(url).text)
            log.info("Feed %s: %d richiami", url, len(found))
        except Exception as exc:  # noqa: BLE001 - il feed è solo una delle fonti
            log.warning("Feed non disponibile (%s): %s", url, exc)
    for url in config.LISTING_URLS:
        try:
            listed = urls_from_listing(fetcher.get(url).text)
            log.info("Elenco %s: %d richiami", url, len(listed))
            found += listed
        except Exception as exc:  # noqa: BLE001
            log.warning("Elenco non disponibile (%s): %s", url, exc)
    return list(dict.fromkeys(found))
