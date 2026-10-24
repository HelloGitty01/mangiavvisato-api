# MangiAvvisato API

*… MezzoSalvato!*

API statica e gratuita dei richiami di prodotti alimentari pubblicati dal
Ministero della Salute. Uno scraper Python gira su GitHub Actions ogni 3 ore,
aggiorna l'archivio in `data/` e pubblica i file JSON su GitHub Pages.

> Dati non ufficiali. Fa fede esclusivamente la pubblicazione sul portale del
> Ministero della Salute.

## Endpoint

Base: `https://hellogitty01.github.io/mangiavvisato-api/`

| Percorso | Contenuto |
|---|---|
| `v1/ultimi.json` | gli ultimi 50 richiami (sintesi) |
| `v1/richiami.json` | tutti i richiami in archivio (sintesi) |
| `v1/revoche.json` | solo le revoche |
| `v1/richiami/{id}.json` | dettaglio completo di un richiamo |
| `v1/meta.json` | data di generazione, totali, versione dello schema |

Esempio di sintesi:

```json
{
  "id": "pop-corn-0",
  "tipo": "richiamo",
  "data_pubblicazione": "2026-08-08",
  "marca": "SALATI PREZIOSI",
  "denominazione": "POP CORN",
  "motivo": "Richiamo per rischio chimico",
  "categoria_rischio": "chimico",
  "lotti": ["L2601", "L2602"],
  "scadenza": "31/12/2026",
  "url": "https://www.salute.gov.it/new/it/ext-avviso-sicurezza-alimentare/pop-corn-0/",
  "pdf_url": "https://www.salute.gov.it/new/sites/default/files/.../modello-richiamo.pdf"
}
```

`url` è la pagina originale del richiamo sul portale del Ministero, `pdf_url`
il modello di richiamo in PDF.

Il dettaglio (`v1/richiami/{id}.json`) contiene tutto ciò che è disponibile:

- `titolo`, `fonte`, date di prima rilevazione e ultimo aggiornamento;
- `allegati`: tutti i documenti e le immagini collegati alla pagina, con titolo e URL;
- `campi_pagina`: tutte le coppie "etichetta: valore" presenti nella pagina;
- `dettagli`: i campi estratti dal modello PDF (denominazione di vendita,
  marchio, operatore, produttore, sede e marchio di identificazione dello
  stabilimento, lotti, scadenza, peso/volume, motivo esteso, avvertenze),
  più `campi_extra` con ogni altra etichetta trovata nel PDF e `testo` con il
  testo integrale del PDF.

## Come funziona

1. **Scoperta**: legge il feed RSS dei richiami e, come riserva, la pagina elenco.
2. **Dettaglio**: per ogni richiamo nuovo apre la pagina e ne estrae marca,
   denominazione, data e motivo.
3. **PDF**: scarica il "Modello di richiamo" e ne estrae lotti, scadenza e
   stabilimento.
4. **Pubblicazione**: rigenera `public/` e la pubblica su GitHub Pages.

L'id di un richiamo è l'ultimo segmento del suo URL sul portale, che è stabile.

### Recupero dello storico

Il portale conserva i richiami dal 2017. Per importarli:
*Actions → Aggiorna richiami → Run workflow*, spunta **backfill** e imposta
**max_new** a `300`. Lo scraper sfoglia tutte le pagine dell'elenco ed elabora
fino a 300 richiami mancanti (circa mezz'ora). Ripeti finché il log dello step
*Scarica i richiami* non riporta `0 nuovi`. Ogni esecuzione salva e pubblica
quello che ha fatto, anche se si interrompe a metà.

### Rielaborazione automatica

Ogni richiamo registra la `versione_parser` con cui è stato estratto. Quando
si migliora un parser si incrementa `PARSER_VERSION` in `scraper/config.py`:
le esecuzioni successive rielaborano da sole i richiami con versione
precedente, sempre entro il limite `max_new`.

### Protezione anti-bot

Il portale del Ministero è protetto da un servizio anti-bot (Gcore) che alla
prima visita mostra una pagina di verifica che richiede JavaScript. Lo scraper
prova prima con una richiesta normale; se riceve la verifica, apre un Chromium
headless con Playwright, la supera e riusa i cookie ottenuti. Se anche così
l'accesso viene negato, il workflow fallisce con codice 2 e GitHub ti avvisa
via email; i dati già pubblicati restano online.

## Pubblicazione su GitHub

1. Crea un repository **pubblico** chiamato `mangiavvisato-api` e carica
   questi file.
2. In `scraper/config.py` sostituisci `HelloGitty01` nello user-agent.
3. *Settings → Pages → Build and deployment → Source*: **GitHub Actions**.
4. *Actions → Aggiorna richiami → Run workflow* per la prima esecuzione.
5. Controlla il log: se tutto va bene trovi l'API all'indirizzo indicato sopra.

## Uso in locale

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium

python -m pytest -q                       # test dei parser
python -m scraper.main -v                 # esecuzione completa
python -m scraper.main --url URL          # un solo richiamo, utile per le prove
python -m scraper.main --only-build       # rigenera public/ dai dati esistenti
python -m scraper.main --backfill --max-new 300   # recupero dello storico
```

## Cose da verificare alla prima esecuzione reale

I parser sono stati scritti sulla struttura attuale del portale ma, per via
della protezione anti-bot, non è stato possibile provarli sulle pagine vere.
Alla prima esecuzione controlla che:

- il feed RSS risponda e contenga link a `/ext-avviso-sicurezza-alimentare/`;
- marca, denominazione, data e motivo vengano estratti (non `null`);
- i campi del PDF, soprattutto `lotti` e `scadenza`, siano corretti. Le
  etichette sono in `PDF_FIELDS` dentro `scraper/parse.py`;
- le revoche vengano riconosciute come `"tipo": "revoca"`.

Quando un parser va corretto, salva la pagina o il testo del PDF in
`tests/fixtures/` e aggiungi un test: così il caso resta coperto.

## Limiti noti

- Le revoche non sono ancora collegate al richiamo originale.
- La paginazione dell'elenco segue il link "successiva" o, in mancanza, il
  parametro `?page=N`: va verificata sul portale alla prima esecuzione con
  backfill (il log mostra quanti richiami trova per pagina).
- GitHub può sospendere i workflow pianificati nei repository inattivi da 60
  giorni; i commit automatici dei dati di norma bastano a evitarlo, ma se
  succede basta riattivarlo da *Actions*.
