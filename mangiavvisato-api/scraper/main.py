"""MangiAvvisato - scraper dei richiami alimentari del Ministero della Salute.

Uso:
    python -m scraper.main                      # esecuzione normale
    python -m scraper.main --url URL_DETTAGLIO  # elabora un solo richiamo (per prove)
    python -m scraper.main --only-build         # rigenera public/ senza scaricare nulla
    python -m scraper.main --backfill --max-new 300  # recupera lo storico, a blocchi
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import config
from .discovery import discover, normalize_url
from .fetch import BlockedError, Fetcher
from .parse import extract_pdf_text, parse_detail, parse_pdf_text, parse_record
from .store import Store, build_public, recall_id

log = logging.getLogger("mangiavvisato")

ROOT = Path(__file__).resolve().parent.parent


# Il page-data.json indica i PDF con un percorso relativo al server interno del
# Ministero: l'indirizzo pubblico si ricava provando i prefissi possibili e si
# memorizza il primo che restituisce davvero un PDF.
PDF_PREFIXES = ["/new", ""]
_pdf_prefix: str | None = None


def download_pdf(fetcher: Fetcher, path: str) -> tuple[str, bytes]:
    global _pdf_prefix
    prefixes = [_pdf_prefix] if _pdf_prefix is not None else PDF_PREFIXES
    last_exc: Exception | None = None
    for prefix in prefixes:
        url = config.BASE_URL + prefix + path
        try:
            content = fetcher.get(url).content
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            continue
        if content.startswith(b"%PDF"):
            if _pdf_prefix is None:
                log.info("Indirizzo pubblico dei PDF: %s<percorso>", config.BASE_URL + prefix)
            _pdf_prefix = prefix
            return url, content
        last_exc = RuntimeError(f"{url} non è un PDF")
    raise last_exc or RuntimeError("PDF non disponibile")


def process(fetcher: Fetcher, url: str, record: dict | None = None) -> dict:
    """Elabora un richiamo: dai dati dell'elenco se disponibili (basta il PDF),
    altrimenti dalla pagina di dettaglio."""
    if record is not None:
        item = parse_record(record, url)
    else:
        item = parse_detail(fetcher.get(url).text, url)
        item["pdf_path"] = None
    item["id"] = recall_id(url)
    item["versione_parser"] = config.PARSER_VERSION
    item["fonte"] = "Ministero della Salute"
    item["dettagli"] = {}
    try:
        if item.get("pdf_path"):
            item["pdf_url"], pdf = download_pdf(fetcher, item["pdf_path"])
        elif item.get("pdf_url"):
            pdf = fetcher.get(item["pdf_url"]).content
        else:
            pdf = None
        if pdf:
            item["dettagli"] = parse_pdf_text(extract_pdf_text(pdf, config.PDF_TIMEOUT))
    except Exception as exc:  # noqa: BLE001 - il PDF è un di più, non deve bloccare
        log.warning("PDF non elaborato per %s: %s", url, exc)
    item.pop("pdf_path", None)
    return item


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", type=Path, default=ROOT / "data")
    ap.add_argument("--out-dir", type=Path, default=ROOT / "public")
    ap.add_argument("--static-dir", type=Path, default=ROOT / "static")
    ap.add_argument("--max-new", type=int, default=config.MAX_NEW_PER_RUN)
    ap.add_argument("--url", action="append", help="elabora solo questo URL di dettaglio (ripetibile)")
    ap.add_argument("--backfill", action="store_true", help="sfoglia tutte le pagine dell'elenco per recuperare lo storico")
    ap.add_argument("--no-browser", action="store_true", help="non usare Playwright per la verifica anti-bot")
    ap.add_argument("--only-build", action="store_true", help="rigenera solo public/ dai dati esistenti")
    ap.add_argument("--new-ids-file", type=Path, default=None, help="scrive qui gli id dei nuovi richiami")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    store = Store(args.data_dir)
    new_ids: list[str] = []
    exit_code = 0

    if not args.only_build:
        fetcher = Fetcher(use_browser_fallback=not args.no_browser)
        try:
            exit_code = run(fetcher, store, args, new_ids)
        finally:
            fetcher.close()
        store.save()

    build_public(store, args.out_dir, args.static_dir)
    if args.new_ids_file:
        args.new_ids_file.write_text("\n".join(new_ids) + ("\n" if new_ids else ""), encoding="utf-8")
    log.info("Nuovi richiami: %d. Totale in archivio: %d", len(new_ids), len(store.items))
    return exit_code


def run(fetcher: Fetcher, store: Store, args, new_ids: list[str]) -> int:
    exit_code = 0
    try:
        urls, records = ([normalize_url(u) for u in args.url], {}) if args.url else discover(fetcher, backfill=args.backfill)
    except BlockedError as exc:
        log.error("%s", exc)
        urls, records = [], {}
        exit_code = 2

    todo = [u for u in urls if args.url or not store.has(recall_id(u))]
    outdated = [] if args.url else [u for u in store.outdated(config.PARSER_VERSION) if u not in todo]
    log.info(
        "Trovati %d richiami: %d nuovi, %d da rielaborare (parser aggiornato)",
        len(urls), len(todo), len(outdated),
    )
    todo += outdated
    if not urls and exit_code == 0:
        log.error("Nessun richiamo trovato: il sito potrebbe aver cambiato struttura o bloccato l'accesso")
        exit_code = 3

    consecutive_failures = 0
    for n, url in enumerate(todo[: args.max_new], start=1):
        try:
            item = process(fetcher, url, records.get(url))
            consecutive_failures = 0
        except BlockedError as exc:
            # Un richiamo irraggiungibile si salta; tre di fila indicano un blocco vero.
            consecutive_failures += 1
            if consecutive_failures >= 3:
                log.error("%s", exc)
                log.error("Tre richiami di fila non raggiungibili: interrompo l'esecuzione")
                exit_code = 2
                break
            log.warning("Salto %s: %s", url, exc)
            continue
        except Exception as exc:  # noqa: BLE001
            log.warning("Errore su %s: %s", url, exc)
            continue
        if not store.has(item["id"]):
            new_ids.append(item["id"])
        store.upsert(item)
        log.info("OK %s - %s (%s)", item["id"], item.get("denominazione"), item.get("tipo"))
        store.save()  # dopo ogni richiamo: un'interruzione non fa perdere il lavoro fatto
    remaining = max(0, len(todo) - args.max_new)
    if remaining:
        log.info("Restano %d richiami da elaborare nelle prossime esecuzioni", remaining)

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
