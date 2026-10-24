"""Estrae il testo da un PDF letto da stdin e lo scrive su stdout.

Gira in un processo separato: se un PDF malformato manda in loop la libreria,
il processo viene terminato allo scadere del tempo senza bloccare lo scraper.
"""

import io
import sys

import pdfplumber


def main() -> None:
    data = sys.stdin.buffer.read()
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        # Il modello di richiamo occupa una o due pagine: oltre la quinta non serve.
        text = "\n".join(page.extract_text() or "" for page in pdf.pages[:5])
    sys.stdout.buffer.write(text.encode("utf-8"))


if __name__ == "__main__":
    main()
