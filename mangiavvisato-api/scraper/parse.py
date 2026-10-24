"""Estrazione dei dati dalla pagina di dettaglio e dal modello PDF del richiamo.

La pagina di dettaglio riporta campi con etichetta ("Marca:", "Denominazione:",
"Data pubblicazione:", "Motivo della segnalazione:") e un link al PDF del
"Modello di richiamo", che contiene lotti, scadenze, stabilimento e così via.
Il parsing si basa sulle etichette di testo, non sulla struttura HTML, così
resiste meglio ai cambi di grafica del portale.
"""

from __future__ import annotations

import io
import re
from datetime import date
from urllib.parse import urljoin

from bs4 import BeautifulSoup

MESI = {
    "gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4, "maggio": 5, "giugno": 6,
    "luglio": 7, "agosto": 8, "settembre": 9, "ottobre": 10, "novembre": 11, "dicembre": 12,
}

DETAIL_LABELS = {
    "data_pubblicazione": "Data pubblicazione",
    "marca": "Marca",
    "denominazione": "Denominazione",
    "motivo": "Motivo della segnalazione",
}


def parse_italian_date(text: str | None) -> str | None:
    """'8 agosto 2026' o '08/08/2026' -> '2026-08-08'."""
    if not text:
        return None
    text = text.strip().lower()
    m = re.search(r"(\d{1,2})\s+([a-zà]+)\s+(\d{4})", text)
    if m and m.group(2) in MESI:
        return date(int(m.group(3)), MESI[m.group(2)], int(m.group(1))).isoformat()
    m = re.search(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})", text)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat()
        except ValueError:
            return None
    return None


def classify_risk(motivo: str | None) -> str:
    t = (motivo or "").lower()
    if "allerg" in t:
        return "allergeni"
    if "microbiolog" in t:
        return "microbiologico"
    if "chimic" in t:
        return "chimico"
    if "fisic" in t:
        return "fisico"
    return "altro"


def parse_detail(html: str, url: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    main = soup.find("main") or soup.body or soup
    lines = [ln.strip() for ln in main.get_text("\n").splitlines() if ln.strip()]

    fields: dict[str, str | None] = {key: None for key in DETAIL_LABELS}
    for i, line in enumerate(lines):
        for key, label in DETAIL_LABELS.items():
            if fields[key] is None and line.lower().startswith(label.lower()):
                value = line[len(label):].lstrip(" :").strip()
                # L'etichetta e il valore possono stare su righe diverse.
                if not value and i + 1 < len(lines):
                    value = lines[i + 1]
                fields[key] = value or None

    h1 = main.find("h1")
    titolo = h1.get_text(" ", strip=True) if h1 else fields["denominazione"]

    pdf_url = None
    for a in main.find_all("a", href=True):
        href = a["href"]
        if href.lower().split("?")[0].endswith(".pdf") or "modello di richiamo" in a.get_text(" ").lower():
            pdf_url = urljoin(url, href)
            break

    text_for_type = f"{titolo or ''} {fields['motivo'] or ''}".lower()
    return {
        "url": url,
        "titolo": titolo,
        "data_pubblicazione": parse_italian_date(fields["data_pubblicazione"]),
        "marca": fields["marca"],
        "denominazione": fields["denominazione"],
        "motivo": fields["motivo"],
        "categoria_rischio": classify_risk(fields["motivo"]),
        "tipo": "revoca" if "revoca" in text_for_type else "richiamo",
        "pdf_url": pdf_url,
    }


# --- Modello PDF di richiamo ------------------------------------------------
# Le etichette seguono il "Modello standard per RICHIAMO-REVOCA" concordato tra
# Ministero e Regioni. I PDF compilati dagli operatori variano parecchio, quindi
# ogni campo ha più varianti e il testo grezzo viene comunque conservato.

PDF_FIELDS = {
    "denominazione_vendita": [r"denominazione di vendita"],
    "marchio": [r"marchio del prodotto"],
    "operatore": [r"nome o ragione sociale dell[' ]?osa", r"ragione sociale"],
    "marchio_identificazione": [r"marchio di identificazione dello stabilimento", r"marchio di identificazione"],
    "produttore": [r"nome del produttore"],
    "sede_stabilimento": [r"sede dello stabilimento"],
    "lotti": [r"lott[oi] di produzione", r"numero di lotto", r"\blott[oi]\b"],
    "scadenza": [r"data di scadenza o termine minimo di conservazione", r"data di scadenza", r"termine minimo di conservazione"],
    "peso_volume": [r"descrizione peso/volume unità di vendita", r"peso/volume", r"unità di vendita"],
    "motivo_esteso": [r"motivo del richiamo"],
    "avvertenze": [r"avvertenze"],
}

def _match_label(line: str) -> tuple[str, str] | None:
    """Se la riga inizia con un'etichetta nota restituisce (campo, resto della riga)."""
    low = line.lower()
    for key, patterns in PDF_FIELDS.items():
        for pattern in patterns:
            m = re.match(r"\s*(?:\d+[.)]\s*)?" + pattern + r"[^:]*:?\s*", low)
            if m:
                return key, line[m.end():].strip()
    return None


def parse_pdf_text(text: str) -> dict:
    """Estrae i campi del modello di richiamo dal testo del PDF, riga per riga.

    Il valore di un campo è il testo dopo l'etichetta più le righe seguenti,
    fino alla prossima etichetta riconosciuta.
    """
    result: dict[str, list[str]] = {}
    current: str | None = None
    for raw in text.splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        if not line:
            continue
        hit = _match_label(line)
        if hit:
            current, rest = hit
            if current in result:  # campo già visto: si ignorano i duplicati
                current = None
                continue
            result[current] = [rest] if rest else []
        elif current:
            result[current].append(line)

    out: dict[str, object] = {}
    for key, chunks in result.items():
        value = " ".join(chunks).strip(" :;-")
        if value:
            out[key] = value
    if "lotti" in out:
        out["lotti"] = split_lots(str(out["lotti"]))
    return out


def split_lots(text: str) -> list[str]:
    parts = re.split(r"\s*(?:[,;]|\be\b|\n)\s*", text)
    return [p.strip() for p in parts if p.strip()]


def extract_pdf_text(pdf_bytes: bytes) -> str:
    import pdfplumber

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)
