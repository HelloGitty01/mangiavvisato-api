"""Archivio persistente (data/richiami.json) e generazione dell'API statica (public/)."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from . import config

SUMMARY_FIELDS = (
    "id", "tipo", "data_pubblicazione", "marca", "denominazione",
    "motivo", "categoria_rischio", "lotti", "scadenza", "url", "pdf_url", "depubblicato",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def recall_id(url: str) -> str:
    """L'ultimo segmento dell'URL di dettaglio è stabile: 'pop-corn-0'."""
    return url.rstrip("/").rsplit("/", 1)[-1]


class Store:
    """Archivio dei richiami: un file JSON per richiamo in data/richiami/.

    Un file per richiamo fa sì che ogni esecuzione salvi nel repository solo
    ciò che è cambiato, invece di riscrivere ogni volta un unico file enorme.
    Il vecchio data/richiami.json, se presente, viene letto e convertito.
    """

    def __init__(self, data_dir: Path):
        self.dir = data_dir / "richiami"
        self.legacy = data_dir / "richiami.json"
        self.items: dict[str, dict] = {}
        self._dirty: set[str] = set()
        if self.legacy.exists():
            self.items.update(json.loads(self.legacy.read_text(encoding="utf-8")) or {})
            self._dirty.update(self.items)  # da convertire al primo salvataggio
        if self.dir.exists():
            for f in sorted(self.dir.glob("*.json")):
                item = json.loads(f.read_text(encoding="utf-8"))
                self.items[item.get("id") or f.stem] = item

    def has(self, rid: str) -> bool:
        return rid in self.items

    def outdated(self, version: int) -> list[str]:
        """URL dei richiami elaborati con una versione dei parser precedente."""
        return [i["url"] for i in self.sorted_items() if i.get("versione_parser", 1) < version]

    def upsert(self, item: dict) -> None:
        old = self.items.get(item["id"], {})
        item["prima_rilevazione"] = old.get("prima_rilevazione", now_iso())
        item["ultimo_aggiornamento"] = now_iso()
        self.items[item["id"]] = item
        self._dirty.add(item["id"])

    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        for rid in sorted(self._dirty):
            item = self.items.get(rid)
            if item is None:
                continue
            tmp = self.dir / f"{rid}.json.tmp"
            tmp.write_text(json.dumps(item, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            tmp.replace(self.dir / f"{rid}.json")
        self._dirty.clear()
        if self.legacy.exists():
            self.legacy.unlink()

    def sorted_items(self) -> list[dict]:
        return sorted(
            self.items.values(),
            key=lambda i: (i.get("data_pubblicazione") or "", i.get("prima_rilevazione") or ""),
            reverse=True,
        )


def summary(item: dict) -> dict:
    flat = {**item, **{k: v for k, v in (item.get("dettagli") or {}).items() if k in ("lotti", "scadenza")}}
    return {k: flat.get(k) for k in SUMMARY_FIELDS}


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def build_public(store: Store, out_dir: Path, static_dir: Path | None = None) -> None:
    """Genera l'API statica. La cartella viene ricreata da zero a ogni esecuzione."""
    if out_dir.exists():
        shutil.rmtree(out_dir)
    v1 = out_dir / "v1"
    items = store.sorted_items()
    summaries = [summary(i) for i in items]

    _write(v1 / "richiami.json", summaries)
    _write(v1 / "ultimi.json", summaries[: config.LATEST_COUNT])
    _write(v1 / "revoche.json", [s for s in summaries if s["tipo"] == "revoca"])
    for item in items:
        _write(v1 / "richiami" / f"{item['id']}.json", item)
    _write(
        v1 / "meta.json",
        {
            "schema_version": config.SCHEMA_VERSION,
            "generato_il": now_iso(),
            "totale": len(items),
            "fonte": "Ministero della Salute - Richiami di prodotti alimentari da parte degli operatori",
            "avvertenza": "Dati non ufficiali. Fa fede esclusivamente la pubblicazione sul portale del Ministero della Salute.",
        },
    )
    (out_dir / ".nojekyll").touch()
    if static_dir and static_dir.exists():
        for f in static_dir.iterdir():
            if f.is_file():
                shutil.copy(f, out_dir / f.name)
