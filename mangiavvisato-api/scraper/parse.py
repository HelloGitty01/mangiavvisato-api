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


FILE_EXTENSIONS = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".odt", ".zip", ".jpg", ".jpeg", ".png", ".webp")
GENERIC_LABEL_RE = re.compile(r"^([A-ZÀ-Ýa-zà-ý][^:]{1,60}):\s*(.*)$")


def _generic_fields(lines: list[str]) -> dict[str, str]:
    """Tutte le coppie 'Etichetta: valore' presenti nella pagina."""
    fields: dict[str, str] = {}
    for i, line in enumerate(lines):
        m = GENERIC_LABEL_RE.match(line)
        if not m or "http" in m.group(1).lower():
            continue
        label, value = m.group(1).strip(), m.group(2).strip()
        if not value and i + 1 < len(lines) and not GENERIC_LABEL_RE.match(lines[i + 1]):
            value = lines[i + 1]
        if value and label not in fields:
            fields[label] = value
    return fields


def _attachments(main, url: str) -> list[dict]:
    """Tutti i documenti collegati alla pagina (PDF, immagini, ecc.)."""
    seen: set[str] = set()
    out = []
    for a in main.find_all("a", href=True):
        href = urljoin(url, a["href"])
        path = href.lower().split("?")[0]
        if path.endswith(FILE_EXTENSIONS) and href not in seen:
            seen.add(href)
            out.append({"titolo": a.get_text(" ", strip=True) or None, "url": href})
    for img in main.find_all("img", src=True):
        src = urljoin(url, img["src"])
        if "/files/" in src and src not in seen:
            seen.add(src)
            out.append({"titolo": img.get("alt") or None, "url": src, "immagine": True})
    return out


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

    allegati = _attachments(main, url)
    pdfs = [a for a in allegati if a["url"].lower().split("?")[0].endswith(".pdf")]
    modello = next((a for a in pdfs if "modello" in (a["titolo"] or "").lower()), None)
    pdf_url = (modello or (pdfs[0] if pdfs else {})).get("url")

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
        "allegati": allegati,
        "campi_pagina": _generic_fields(lines),
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

    # Ogni altra riga 'Etichetta: valore' non riconosciuta finisce in campi_extra.
    extra: dict[str, str] = {}
    for raw in text.splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        m = GENERIC_LABEL_RE.match(line)
        if m and not _match_label(line) and m.group(2).strip():
            extra.setdefault(m.group(1).strip(), m.group(2).strip())
    if extra:
        out["campi_extra"] = extra
    out["testo"] = text.strip()
    return out


def split_lots(text: str) -> list[str]:
    parts = re.split(r"\s*(?:[,;]|\be\b|\n)\s*", text)
    return [p.strip() for p in parts if p.strip()]


def extract_pdf_text(pdf_bytes: bytes) -> str:
    import pdfplumber

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)
