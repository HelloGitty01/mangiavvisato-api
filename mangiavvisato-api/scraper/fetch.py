"""Accesso HTTP al portale del Ministero.

Il sito è protetto da un servizio anti-bot (Gcore) che mostra una pagina di
"Browser Validation": un JavaScript verifica il browser, imposta un cookie e
ricarica la pagina. Strategia:
  1. si prova con una normale richiesta HTTP (veloce, se il sito la accetta);
  2. alla prima pagina di validazione si avvia un vero Chromium con Playwright
     e da quel momento tutte le richieste passano dal browser, che mantiene
     cookie e "impronta" coerenti;
  3. le pagine HTML e il feed si aprono nella scheda del browser; i PDF si
     scaricano con il client HTTP del browser, che condivide gli stessi cookie.

Variabili d'ambiente:
  MANGIAVVISATO_HEADED=1  avvia Chromium con finestra (sul runner GitHub serve
                          xvfb-run): è il modo meno riconoscibile come bot.
  MANGIAVVISATO_DEBUG_DIR cartella dove salvare screenshot e HTML quando la
                          verifica non viene superata.
"""

from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path

import requests

from . import config

log = logging.getLogger(__name__)

CHALLENGE_MARKERS = (
    "browser validation page",
    "please enable cookies",
    "is using security service for protection",
)

# Tempo massimo di attesa perché la verifica si completi, in secondi.
CHALLENGE_WAIT = 30


class BlockedError(RuntimeError):
    """Il sito ha rifiutato l'accesso anche passando dal browser."""


def looks_like_challenge(body: bytes | str, content_type: str = "text/html") -> bool:
    if "html" not in content_type.lower():
        return False
    if isinstance(body, bytes):
        body = body[:8000].decode("utf-8", errors="ignore")
    head = body[:8000].lower()
    return any(marker in head for marker in CHALLENGE_MARKERS)


def is_challenge(response) -> bool:
    if response.status_code in (403, 429, 503):
        return True
    return looks_like_challenge(response.text, response.headers.get("Content-Type", ""))


def decode_body(body: bytes, content_type: str = "") -> str:
    """Decodifica rispettando il charset dichiarato. Il portale non usa sempre
    UTF-8: decodificare alla cieca rovina accenti e trattini."""
    m = re.search(r"charset=([\w-]+)", content_type or "", re.I) or re.search(
        rb"<meta[^>]+charset=[\"']?([\w-]+)", body[:4000], re.I
    )
    if m:
        charset = m.group(1).decode() if isinstance(m.group(1), bytes) else m.group(1)
        try:
            return body.decode(charset)
        except (LookupError, UnicodeDecodeError):
            pass
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        return body.decode("cp1252", errors="replace")


class SimpleResponse:
    """Risposta minimale, compatibile con l'uso che ne fa lo scraper."""

    def __init__(self, url: str, status: int, headers: dict, body: bytes):
        self.url = url
        self.status_code = status
        self.headers = {k.title(): v for k, v in headers.items()}
        self.content = body

    @property
    def text(self) -> str:
        return decode_body(self.content, self.headers.get("Content-Type", ""))

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code} su {self.url}")


class Fetcher:
    def __init__(self, use_browser_fallback: bool = True, delay: float = config.REQUEST_DELAY):
        self.use_browser_fallback = use_browser_fallback
        self.delay = delay
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": config.USER_AGENT, "Accept-Language": "it-IT,it;q=0.9"})
        self.data_requests: list[str] = []
        self.last_html_url: str | None = None
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None
        self.debug_dir = Path(os.environ["MANGIAVVISATO_DEBUG_DIR"]) if os.environ.get("MANGIAVVISATO_DEBUG_DIR") else None

    # --- interfaccia pubblica -------------------------------------------------

    def get(self, url: str):
        if self._page is None:
            response = self.session.get(url, timeout=config.REQUEST_TIMEOUT)
            time.sleep(self.delay)
            if not is_challenge(response):
                response.raise_for_status()
                return response
            log.info("Pagina di validazione anti-bot su %s", url)
            if not self.use_browser_fallback:
                raise BlockedError(f"Accesso bloccato dalla protezione anti-bot: {url}")
            self._start_browser()
        response = self._browser_get(url)
        time.sleep(self.delay)
        response.raise_for_status()
        return response

    def close(self) -> None:
        if self._browser:
            self._browser.close()
        if self._pw:
            self._pw.stop()
        self._pw = self._browser = self._context = self._page = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # --- browser ----------------------------------------------------------------

    def _start_browser(self) -> None:
        from playwright.sync_api import sync_playwright

        headed = os.environ.get("MANGIAVVISATO_HEADED") == "1"
        log.info("Avvio Chromium (%s) per la verifica anti-bot", "con finestra" if headed else "headless")
        self._pw = sync_playwright().start()
        args = ["--disable-blink-features=AutomationControlled"]
        try:
            # channel="chromium" usa il browser completo anche in headless,
            # meno riconoscibile della versione "headless shell".
            self._browser = self._pw.chromium.launch(headless=not headed, channel="chromium", args=args)
        except Exception:  # noqa: BLE001
            self._browser = self._pw.chromium.launch(headless=not headed, args=args)
        self._context = self._browser.new_context(
            user_agent=config.BROWSER_USER_AGENT,
            locale="it-IT",
            timezone_id="Europe/Rome",
            viewport={"width": 1366, "height": 900},
        )
        self._context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        # Nessuna operazione del browser può attendere più di un minuto.
        self._context.set_default_timeout(60_000)
        self._context.set_default_navigation_timeout(60_000)
        self._page = self._context.new_page()
        # Richieste dati fatte dalla pagina (XHR/fetch): utili per capire da dove
        # una pagina costruita con JavaScript prende i suoi contenuti.
        self.data_requests: list[str] = []
        self._page.on(
            "request",
            lambda req: self.data_requests.append(f"{req.method} {req.resource_type} {req.url}")
            if req.resource_type in ("xhr", "fetch") else None,
        )

    def _wait_challenge_cleared(self) -> bool:
        """Attende che la pagina di validazione sparisca. La pagina può essere
        in pieno reindirizzamento: in quel caso leggerne il contenuto fallisce,
        e si riprova dopo un attimo."""
        page = self._page
        deadline = time.monotonic() + CHALLENGE_WAIT
        while time.monotonic() < deadline:
            try:
                page.wait_for_load_state("domcontentloaded", timeout=5000)
                html = page.content()
            except Exception:  # noqa: BLE001 - pagina in navigazione
                page.wait_for_timeout(500)
                continue
            if not looks_like_challenge(html):
                return True
            page.wait_for_timeout(1000)
        return False

    def _is_file(self, url: str) -> bool:
        path = url.lower().split("?")[0]
        return path.endswith((".pdf", ".xml", ".rss", ".doc", ".docx", ".xls", ".xlsx", ".zip", ".jpg", ".jpeg", ".png"))

    def reset_browser(self) -> None:
        """Chiude il browser: la richiesta successiva lo riavvia da capo."""
        try:
            self.close()
        except Exception:  # noqa: BLE001
            self._pw = self._browser = self._context = self._page = None

    def _browser_get(self, url: str) -> SimpleResponse:
        last_status = None
        for attempt in range(1, 4):
            try:
                if self._is_file(url):
                    # File e feed: client HTTP del browser, con gli stessi cookie.
                    r = self._context.request.get(url, timeout=30_000)
                    status, headers, body = r.status, r.headers, r.body()
                    ctype = headers.get("content-type", "")
                    last_status = status
                    if status not in (403, 429, 503) and not looks_like_challenge(body, ctype):
                        return SimpleResponse(url, status, headers, body)
                    # Verifica da superare: si apre una pagina HTML del sito e si riprova.
                    log.info("Verifica anti-bot in corso su %s (tentativo %d)", url, attempt)
                    self._page.goto(config.BASE_URL + "/new/it/", wait_until="domcontentloaded", timeout=60_000)
                    self._wait_challenge_cleared()
                    continue

                # Pagine HTML: si legge il DOM dopo che la verifica e il JavaScript
                # della pagina hanno finito, già decodificato correttamente.
                r = self._page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                status = r.status if r is not None else 200
                last_status = status
                if not self._wait_challenge_cleared():
                    log.info("Verifica anti-bot non completata su %s (tentativo %d)", url, attempt)
                    continue
                try:
                    self._page.wait_for_load_state("networkidle", timeout=10_000)
                except Exception:  # noqa: BLE001 - pagine con richieste continue
                    pass
                html = self._page.content()
                self.last_html_url = url
                if looks_like_challenge(html):
                    continue
                # Lo stato della prima risposta può essere quello della verifica:
                # se ora la pagina è quella vera, vale come 200 (tranne il 404).
                final_status = 404 if status == 404 else 200
                return SimpleResponse(url, final_status, {"content-type": "text/html; charset=utf-8"}, html.encode("utf-8"))
            except Exception as exc:  # noqa: BLE001
                log.info("Tentativo %d su %s non riuscito: %s", attempt, url, str(exc).splitlines()[0])
                try:
                    self._page.wait_for_timeout(2000)
                except Exception:  # noqa: BLE001
                    pass

        self._save_debug(url)
        raise BlockedError(f"Accesso bloccato dalla protezione anti-bot: {url} (ultimo stato HTTP {last_status})")

    def save_snapshot(self, name: str, html: str) -> None:
        """Salva HTML e screenshot della pagina corrente nella cartella di debug."""
        if not self.debug_dir:
            return
        try:
            self.debug_dir.mkdir(parents=True, exist_ok=True)
            (self.debug_dir / f"{name}.html").write_text(html, encoding="utf-8")
            if self._page is not None:
                self._page.screenshot(path=str(self.debug_dir / f"{name}.png"), full_page=True)
        except Exception as exc:  # noqa: BLE001
            log.info("Copia di debug non salvata: %s", exc)

    def _save_debug(self, url: str) -> None:
        if not (self.debug_dir and self._page):
            return
        try:
            self.debug_dir.mkdir(parents=True, exist_ok=True)
            name = url.rstrip("/").rsplit("/", 1)[-1] or "pagina"
            self._page.screenshot(path=str(self.debug_dir / f"{name}.png"), full_page=True)
            (self.debug_dir / f"{name}.html").write_text(self._page.content(), encoding="utf-8")
            log.info("Salvati screenshot e HTML di debug in %s", self.debug_dir)
        except Exception as exc:  # noqa: BLE001
            log.info("Debug non salvato: %s", exc)
