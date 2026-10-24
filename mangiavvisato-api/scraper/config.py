"""Configurazione dello scraper MangiAvvisato.

Gli URL del portale del Ministero cambiano di tanto in tanto: se lo scraper
smette di trovare richiami, il primo posto da controllare è questo file.
"""

BASE_URL = "https://www.salute.gov.it"

# Feed RSS dei "Richiami di prodotti alimentari da parte degli operatori".
FEED_URLS = [
    "https://www.salute.gov.it/new/rss/RSS_avvisi_richiami_osa.xml",
]

# Pagine elenco usate come rete di sicurezza se il feed non risponde.
LISTING_URLS = [
    "https://www.salute.gov.it/new/it/avvisi/avvisi-e-richiami-di-prodotti-alimentari/",
]

# Le pagine di dettaglio dei richiami contengono questo frammento nel percorso.
DETAIL_PATH_MARKER = "/ext-avviso-sicurezza-alimentare/"

# User-agent identificabile: chi gestisce il sito deve poter capire chi siamo.
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36 MangiAvvisatoBot/1.0 "
    "(+https://github.com/HelloGitty01/mangiavvisato-api)"
)

# Pausa tra una richiesta e l'altra, in secondi. Siamo ospiti: niente fretta.
REQUEST_DELAY = 2.0
REQUEST_TIMEOUT = 30

# Numero massimo di nuovi richiami elaborati in una singola esecuzione.
MAX_NEW_PER_RUN = 40

# Quanti richiami recenti finiscono in ultimi.json.
LATEST_COUNT = 50

SCHEMA_VERSION = 1
