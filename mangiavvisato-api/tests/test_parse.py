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
