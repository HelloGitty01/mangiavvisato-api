"""Accesso HTTP al portale del Ministero.

Il sito è protetto da un servizio anti-bot (Gcore) che, alla prima visita,
mostra una pagina di "Browser Validation" che richiede JavaScript e cookie.
Strategia:
  1. si prova con una normale richiesta HTTP;
  2. se arriva la pagina di validazione, si apre un Chromium headless con
     Playwright, si supera la verifica e si copiano i cookie nella sessione;
  3. le richieste successive riusano quei cookie (stesso user-agent).
"""

from __future__ import annotations

import logging
import time

import requests

from . import config

log = logging.getLogger(__name__)

CHALLENGE_MARKERS = (
    "browser validation page",
    "please enable cookies",
    "is using security service for protection",
)


class BlockedError(RuntimeError):
    """Il sito ha rifiutato l'accesso anche dopo il tentativo con il browser."""


def is_challenge(response: requests.Response) -> bool:
    if response.status_code in (403, 429, 503):
        return True
    ctype = response.headers.get("Content-Type", "")
    if "html" not in ctype:
        return False
    head = response.text[:5000].lower()
    return any(marker in head for marker in CHALLENGE_MARKERS)


class Fetcher:
    def __init__(self, use_browser_fallback: bool = True, delay: float = config.REQUEST_DELAY):
        self.use_browser_fallback = use_browser_fallback
        self.delay = delay
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": config.USER_AGENT,
                "Accept-Language": "it-IT,it;q=0.9",
            }
        )
        self._browser_tried = False

    def get(self, url: str) -> requests.Response:
        response = self._raw_get(url)
        if is_challenge(response):
            log.info("Pagina di validazione anti-bot su %s", url)
            if self.use_browser_fallback and not self._browser_tried:
                self._solve_with_browser(url)
                response = self._raw_get(url)
            if is_challenge(response):
                raise BlockedError(f"Accesso bloccato dalla protezione anti-bot: {url}")
        response.raise_for_status()
        return response

    def _raw_get(self, url: str) -> requests.Response:
        response = self.session.get(url, timeout=config.REQUEST_TIMEOUT)
        time.sleep(self.delay)
        return response

    def _solve_with_browser(self, url: str) -> None:
        """Supera la verifica con Chromium headless e copia i cookie ottenuti."""
        self._browser_tried = True
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            log.warning("Playwright non installato: impossibile superare la verifica anti-bot")
            return

        log.info("Avvio Chromium headless per la verifica anti-bot")
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(user_agent=config.USER_AGENT, locale="it-IT")
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            # Attende fino a 30 secondi che la pagina di validazione sparisca.
            for _ in range(30):
                content = page.content().lower()
                if not any(marker in content for marker in CHALLENGE_MARKERS):
                    break
                page.wait_for_timeout(1000)
            for cookie in context.cookies():
                self.session.cookies.set(
                    cookie["name"],
                    cookie["value"],
                    domain=cookie.get("domain"),
                    path=cookie.get("path", "/"),
                )
            browser.close()
