import json
from pathlib import Path

from scraper.discovery import urls_from_feed, urls_from_listing
from scraper.fetch import is_challenge
from scraper.parse import classify_risk, parse_detail, parse_italian_date, parse_pdf_text
from scraper.store import Store, build_public, recall_id

FIX = Path(__file__).parent / "fixtures"
URL = "https://www.salute.gov.it/new/it/ext-avviso-sicurezza-alimentare/pop-corn-0/"


def test_date():
    assert parse_italian_date("8 agosto 2026") == "2026-08-08"
    assert parse_italian_date("05/11/2026") == "2026-11-05"
    assert parse_italian_date("boh") is None


def test_risk():
    assert classify_risk("Richiamo per rischio microbiologico") == "microbiologico"
    assert classify_risk("Richiamo per presenza di allergeni non dichiarati") == "allergeni"


def test_detail():
    item = parse_detail((FIX / "dettaglio.html").read_text(), URL)
    assert item["titolo"] == "POP CORN"
    assert item["data_pubblicazione"] == "2026-08-08"
    assert item["marca"] == "SALATI PREZIOSI"
    assert item["denominazione"] == "POP CORN"
    assert item["categoria_rischio"] == "chimico"
    assert item["tipo"] == "richiamo"
    assert item["pdf_url"] == "https://www.salute.gov.it/new/sites/default/files/2026-08/modello-richiamo-pop-corn.pdf"


def test_feed():
    assert urls_from_feed((FIX / "feed.xml").read_text()) == [URL]


def test_listing():
    html = '<a href="/new/it/ext-avviso-sicurezza-alimentare/pop-corn-0/">x</a><a href="/altro/">y</a>'
    assert urls_from_listing(html) == [URL]


def test_pdf_fields():
    d = parse_pdf_text((FIX / "modello.txt").read_text())
    assert d["marchio"] == "SALATI PREZIOSI"
    assert d["lotti"] == ["L2601", "L2602", "L26/03"]
    assert d["scadenza"] == "31/12/2026"
    assert d["marchio_identificazione"] == "IT 123 CE"
    assert d["sede_stabilimento"].startswith("Via Roma 1")
    assert d["peso_volume"] == "100 g"


def test_challenge_detection():
    class R:
        status_code = 200
        headers = {"Content-Type": "text/html"}
        text = "<h2>Browser Validation Page</h2> www.salute.gov.it is using security service for protection"
    assert is_challenge(R())


def test_build_public(tmp_path):
    store = Store(tmp_path / "data")
    item = parse_detail((FIX / "dettaglio.html").read_text(), URL)
    item["id"] = recall_id(URL)
    item["dettagli"] = parse_pdf_text((FIX / "modello.txt").read_text())
    store.upsert(item)
    store.save()
    out = tmp_path / "public"
    build_public(store, out)
    summ = json.loads((out / "v1" / "richiami.json").read_text())
    assert summ[0]["id"] == "pop-corn-0"
    assert summ[0]["lotti"] == ["L2601", "L2602", "L26/03"]
    assert (out / "v1" / "richiami" / "pop-corn-0.json").exists()
    assert json.loads((out / "v1" / "meta.json").read_text())["totale"] == 1


def test_detail_all_details():
    item = parse_detail((FIX / "dettaglio.html").read_text(), URL)
    # Il modello di richiamo è preferito agli altri PDF.
    assert item["pdf_url"].endswith("modello-richiamo-pop-corn.pdf")
    urls = [a["url"] for a in item["allegati"]]
    assert any(u.endswith("nota-integrativa.pdf") for u in urls)
    assert any(u.endswith("foto-pop-corn.jpg") for u in urls)
    assert item["campi_pagina"]["Area"] == "Sicurezza alimentare"
    assert item["campi_pagina"]["Marca"] == "SALATI PREZIOSI"


def test_pdf_extra_and_text():
    d = parse_pdf_text((FIX / "modello.txt").read_text())
    assert d["campi_extra"]["Codice EAN"] == "8001234567890"
    assert "Marchio del prodotto" in d["testo"]


def test_summary_has_links(tmp_path):
    store = Store(tmp_path / "data")
    item = parse_detail((FIX / "dettaglio.html").read_text(), URL)
    item["id"] = recall_id(URL)
    store.upsert(item)
    out = tmp_path / "public"
    build_public(store, out)
    s = json.loads((out / "v1" / "richiami.json").read_text())[0]
    assert s["url"] == URL
    assert s["pdf_url"].endswith(".pdf")


def test_outdated(tmp_path):
    store = Store(tmp_path / "data")
    store.upsert({"id": "a", "url": "https://x/a/", "versione_parser": 1})
    store.upsert({"id": "b", "url": "https://x/b/", "versione_parser": 2})
    assert store.outdated(2) == ["https://x/a/"]


class FakeFetcher:
    """Simula tre pagine di elenco; la terza ripete la seconda (fine elenco)."""

    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def get(self, url):
        self.calls.append(url)

        class R:
            text = self.pages.get(url, "")
        return R()


def _page(ids, next_href=None):
    links = "".join(f'<a href="/new/it/ext-avviso-sicurezza-alimentare/{i}/">{i}</a>' for i in ids)
    nav = f'<a rel="next" href="{next_href}">Successiva</a>' if next_href else ""
    return f"<main>{links}{nav}</main>"


def test_crawl_listing_follows_next_link():
    from scraper.discovery import crawl_listing
    base = "https://www.salute.gov.it/new/it/avvisi/elenco/"
    pages = {
        base: _page(["a", "b"], "?page=1"),
        base + "?page=1": _page(["c"], "?page=2"),
        base + "?page=2": _page(["c"]),
    }
    f = FakeFetcher(pages)
    urls = crawl_listing(f, base, max_pages=10)
    assert [u.rstrip("/").rsplit("/", 1)[-1] for u in urls] == ["a", "b", "c"]


def test_crawl_listing_fallback_page_param():
    from scraper.discovery import crawl_listing
    base = "https://www.salute.gov.it/new/it/avvisi/elenco/"
    pages = {base: _page(["a"]), base + "?page=1": _page(["b"]), base + "?page=2": ""}
    urls = crawl_listing(FakeFetcher(pages), base, max_pages=10)
    assert len(urls) == 2
