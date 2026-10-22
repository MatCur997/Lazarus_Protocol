"""Impostazioni e collegamento con app_lazarus. Gli altri moduli le leggono con `import config as cfg`
(es. cfg.MODELLO): così un valore cambiato all'avvio (--modello, --dati) vale per tutti."""
import sys
from pathlib import Path

# ---------------------------------------------------------------- configurazione
QUI = Path(__file__).resolve().parent                 # interfaccia/backend (codice Python)
INTERFACCIA = QUI.parent                              # interfaccia/
PROGETTO = INTERFACCIA.parent                         # Lazarus_Protocol/
CARTELLA_DATI = None                                  # cartella letta a ogni avvio: solo con --dati (i documenti si caricano a mano)
CARTELLA_CARICATI = INTERFACCIA / 'caricati'          # file aggiunti con il pulsante +
CARTELLA_REGISTRO = INTERFACCIA / 'registro'          # storico, cache embedding, esclusioni
DIARIO_NOTEBOOK = PROGETTO / 'lazzaro' / 'diario_chat.jsonl'
STATICI = INTERFACCIA / 'frontend'                    # pagine, stile e script del browser

OLLAMA = 'http://127.0.0.1:11434'
MODELLO = 'gemma4:12b'
MODELLO_EMBEDDING = 'bge-m3'      # se manca in Ollama si usa solo la ricerca per parole chiave
TOP_K = 10                # brani migliori quando i documenti pertinenti non ci stanno tutti nel prompt
MAX_BRANI_PER_DOC = 2     # un documento lungo (o un intruso) non può occupare tutti i posti
# ---- RAG: come si scelgono i brani (parole chiave + significato, fusi con RRF)
# I documenti NON pertinenti all'indagine (clustering in pertinenza.py) arrivano al modello solo se la domanda li riguarda,
# e non diventano mai indizi, eventi o luoghi.
HYDE = True                # HyDE: il modello scrive una risposta ipotetica, la si cerca per significato come terza classifica
HYDE_SOGLIA = 0.60         # HyDE solo se la ricerca per significato trova poco (migliore similarità sotto soglia). 1 = sempre
HYDE_TOKEN = 120           # lunghezza massima della risposta ipotetica
# Se il controllo di pertinenza deve chiamare il modello (~1,7 s), la ricerca parte nello stesso momento invece che dopo; se la domanda risulta
# non sul caso il risultato si scarta. False = prima il controllo, poi la ricerca, come prima.
PERTINENZA_IN_PARALLELO = True
# Dopo le regole e prima del modello: se la domanda assomiglia ai brani del caso almeno quanto questa soglia (coseno, come HYDE_SOGLIA) è sul
# caso e il modello non viene chiamato (~1,7 s). Sotto soglia decide il modello come prima. Soglia non ancora tarata sulle domande reali:
# alzala se passano come "sul caso" domande estranee. False = sempre il modello.
PERTINENZA_CON_VETTORI = False   # spento: misurato con Ollama, la somiglianza domanda-brani è 0,48-0,62 anche per domande estranee (0,58), la soglia non separa
PERTINENZA_SOGLIA_VETTORI = 0.60
HYDE_SALTA_CON_IDENTIFICATORI = True   # la domanda ha parole rare (codici, nomi) che trovano già dei brani: HyDE (~2 s) non si fa. False = come prima
# Fusione delle classifiche (RRF pesata). Gli identificatori sono le parole della domanda che compaiono in pochissimi brani
# (codici, pratiche, nomi rari): una corrispondenza esatta su una di esse vale più di una somiglianza di significato, che tra
# documenti quasi identici è rumore. Misurato con misure/misura_ricerca.py: senza questa classifica il documento primo in BM25
# con un margine netto veniva superato da brani solo simili per significato.
PESI_RRF = {'bm25': 1.0, 'semantica': 1.0, 'hyde': 1.0, 'identificatori': 2.0, 'riscritta_bm25': 0.7, 'riscritta_semantica': 0.7}
IDENT_DF_FRAZIONE = 0.002  # una parola è un identificatore se compare in meno di questa frazione dei brani DEL CASO...
IDENT_DF_MIN = 5           # ...ma sempre se compare al massimo in questi brani del caso. Il minimo conta quando i brani del caso sono pochi (centinaia):
                           # con 3 un numero di pratica citato in 4 bollettini smetteva di essere un identificatore (misurato: 2 domande su codici perse su 40)
IDENT_MAX_BRANI = 30       # lunghezza massima della classifica degli identificatori
# Diversità dei brani scelti (MMR): con centinaia di documenti costruiti sullo stesso modello le varianti di un bollettino riempiono tutti
# i posti e il documento diverso (un collaudo, un esito) resta fuori. 1 = nessuna diversificazione (i TOP_K migliori, come prima).
# Misurato con misure/misura_ricerca.py sulle domande su un segnale con centinaia di bollettini quasi identici: documento dell'esito tra le fonti 1/6 (1.0), 2/6 (0.7), 3/6 (0.5 e 0.3);
# le altre misure invariate (12/12 a mano, 40/40 codici).
MMR_LAMBDA = 0.5
COPIE_SOGLIA = 0.97        # brani con somiglianza (coseno) da qui in su sono copie: tra i candidati ne resta uno, con l'elenco delle altre. 1 = spento
MMR_POOL = 40              # candidati tra cui si sceglie (i migliori per punteggio, al massimo MAX_BRANI_PER_DOC per documento)
# Domande di seguito ("e lui?", "e dopo?"): una chiamata breve le rende autonome con i nomi degli scambi visibili,
# prima di pertinenza, ricerca e risposta. Parte solo se la domanda sembra dipendere dalla conversazione.
RISCRIVI_DOMANDE = True
RISCRIVI_TURNI = 2         # quanti scambi precedenti legge chi riscrive
RISCRIVI_TOKEN = 80        # lunghezza massima della domanda riscritta
RISCRIVI_MAX_CARATTERI = 300
ISTRUZIONI_RISCRITTURA = (
    "Riscrivi l'ULTIMA domanda dell'utente come domanda autonoma, comprensibile senza la conversazione. "
    'Cambia il MINIMO: tieni tutte le parole della domanda e sostituisci soltanto i pronomi e i rimandi (lui, lì, quello, "e dopo?", '
    '"e l\'altro?") con i nomi di persone, luoghi, documenti o codici precisi che compaiono negli scambi precedenti. '
    'Se il rimando è generico o riguarda tutta la conversazione ("questi avvenimenti", "tutto questo", "quanto detto"), lascialo com\'è: '
    'non riassumere gli scambi dentro la domanda. Se la domanda è già autonoma, ripetila identica. '
    "Non rispondere, non aggiungere fatti o dettagli nuovi, non cambiare l'argomento, non riportare citazioni tra parentesi quadre. "
    'Una sola frase, solo la domanda.'
)
ISTRUZIONI_HYDE = (
    'Scrivi in italiano un breve brano (3-4 frasi) che potrebbe comparire in un documento d\'archivio '
    '(verbale, registro, appunto, rapporto) e che risponde alla domanda. Usa il lessico tipico di quei documenti. '
    'È solo un esempio per cercare documenti simili: non serve che sia vero, non aggiungere commenti né premesse.'
)
NUM_CTX = 12288            # come lazarus.py (riserva se app_lazarus non si collega)
NUM_PREDICT = 350
NUM_PREDICT_CONVERSAZIONE = 80
TEMPERATURA = 0.5
PORTA = 8765
MAX_UPLOAD = 50 * 1024 * 1024
# Luoghi, indizi ed eventi: estratti dal modello DOPO la risposta, quindi senza rallentarla.
# Se arriva una nuova domanda l'estrazione si interrompe e si usano le regole. False = solo regole.
ESTRAZIONE_CON_MODELLO = False   # True = seconda chiamata al modello dopo ogni risposta: occupa Ollama e rallenta la domanda dopo
# Fino a quanti documenti si passano tutti i testi al modello: NON sono numeri fissi, dipendono dal contesto del modello (NUM_CTX, che arriva da lazarus.py:
# vedi sotto, dopo il collegamento). Ai documenti va un terzo del contesto; il resto è per istruzioni, cronologia, domanda e risposta.
FRAZIONE_CONTESTO_DOCUMENTI = 1 / 3
CARATTERI_PER_TOKEN = 3          # stima prudente per l'italiano
CARATTERI_PER_BRANO = 300        # il numero massimo di brani è lo spazio diviso per questa lunghezza: tanti brani corti sono costosi per i marcatori [ID]
MEMORIA_TURNI = 0                # domande precedenti rimandate al modello (0 = come lazarus.py: nessuna memoria)
NOTA_GIORNI_MAX_ID = 15          # la cronologia nel prompt elenca gli ID di un giorno solo fino a questo numero (poi ne dice quanti sono)

ISTRUZIONI_ELEMENTI = (
    'Estrai dalla risposta e dalle fonti gli elementi utili all\'indagine: '
    'luoghi importanti, indizi (prove, dettagli sospetti, incongruenze) ed eventi significativi (fatti accaduti). '
    'Usa solo elementi presenti nelle fonti. Per ognuno indica il documento (es. G1-02) e una "citazione" '
    'copiata parola per parola dalla fonte. Titoli brevi, al massimo 6 parole. Al massimo 3 elementi per tipo. '
    'Se un tipo non ha elementi, lascia la lista vuota.'
)
_ITEM = lambda campo: {'type': 'array', 'items': {'type': 'object', 'required': [campo, 'doc', 'citazione'], 'properties': {
    campo: {'type': 'string'}, 'doc': {'type': 'string'}, 'citazione': {'type': 'string'}}}}
SCHEMA_ELEMENTI = {'type': 'object', 'required': ['luoghi', 'indizi', 'eventi'],
                   'properties': {'luoghi': _ITEM('nome'), 'indizi': _ITEM('titolo'), 'eventi': _ITEM('titolo')}}

# ---- Collegamento con app_lazarus: modello, istruzioni e token sono quelli di lazarus.py, l'UNICO posto dove stanno ----
# Cerca la cartella app_lazarus (variabile LAZARUS_APP, oppure accanto a Lazarus_UI). Senza, l'interfaccia non parte: non esistono
# istruzioni di riserva duplicate qui (due copie dello stesso prompt si allineano male). lazarus.py si importa anche senza `requests`.
LAZARUS = None
AVVISO_LAZARUS = None            # avvisi di collegamento, mostrati nell'interfaccia (oggi sempre vuoto: il collegamento o riesce o ferma l'avvio)


def _collega_app_lazarus():
    global LAZARUS, MODELLO, NUM_PREDICT, NUM_CTX, NUM_PREDICT_CONVERSAZIONE, TEMPERATURA
    import os
    candidati = [os.environ.get('LAZARUS_APP')] + [p / 'app_lazarus' for p in QUI.parents]
    for c in candidati:
        if c and (Path(c) / 'lazarus.py').is_file():
            sys.path.insert(0, str(c))
            import lazarus as lz
            LAZARUS = lz
            MODELLO, NUM_PREDICT, NUM_CTX = lz.MODELLO, lz.MAX_TOKEN, lz.NUM_CTX
            NUM_PREDICT_CONVERSAZIONE, TEMPERATURA = lz.MAX_TOKEN_CONVERSAZIONE, lz.TEMPERATURA
            print(f'Collegato a app_lazarus: {Path(c)} (modello {MODELLO}, istruzioni di lazarus.py)')
            return
    raise SystemExit('Cartella app_lazarus non trovata: contiene le istruzioni e il modello. Impostala con la variabile LAZARUS_APP.')


_collega_app_lazarus()
SOGLIA_CARATTERI = int(NUM_CTX * FRAZIONE_CONTESTO_DOCUMENTI * CARATTERI_PER_TOKEN)      # NUM_CTX 12288 → 12288 caratteri (circa 4000 token)
SOGLIA_BRANI = SOGLIA_CARATTERI // CARATTERI_PER_BRANO                                      # → 40 brani


def istruzioni_correnti():
    return LAZARUS.leggi_istruzioni()


def prepara_richiesta(corpo):
    """Ogni richiesta al modello è indipendente dalle precedenti e non riempie il contesto (vedi lazarus.py)."""
    return LAZARUS.prepara_richiesta(corpo)


def classifica_domanda(domanda, modello, titoli, nomi, demo=False):
    """(conversazione, motivo): il messaggio riguarda il caso? Si decide prima di cercare nei documenti.
    Regole e controllo col modello sono in lazarus.py; in demo solo le regole (non c'è un modello)."""
    if demo:
        return (True, 'saluto o cortesia (regole)') if LAZARUS.e_conversazione(domanda) else (False, 'demo: solo regole')
    return LAZARUS.classifica_domanda(domanda, modello, titoli, nomi)


def istruzioni_conversazione():
    return LAZARUS.ISTRUZIONI_CONVERSAZIONE


ESTENSIONI_TESTO = {'.txt', '.md', '.csv', '.json', '.jsonl', '.log'}
ESTENSIONI_MARKUP = {'.xml', '.html', '.htm'}     # testo con tag: si tengono solo i contenuti
ESTENSIONI_OK = ESTENSIONI_TESTO | ESTENSIONI_MARKUP | {'.docx', '.pdf', '.xlsx', '.zip'}

STRUMENTI = {
    'rispondi_dalla_cronologia': 'Domande sulla chat ("cosa ti ho appena chiesto?"): la risposta si prende dagli scambi visibili, senza modello.',
    'controlla_pertinenza':'Decide se il messaggio riguarda il caso (regole, poi il modello con i soli titoli): se no, si conversa senza documenti.',
    'cerca_parole_chiave': 'Ricerca BM25 sulle parole della domanda (ottima per nomi e codici).',
    'cerca_semantica': 'Ricerca per significato con embedding locali in Ollama.',
    'cerca_hyde': 'HyDE: il modello scrive una risposta ipotetica e si cercano i brani con significato simile (solo se la ricerca semantica trova poco).',
    'cerca_riscritta': 'Domanda di seguito: si cerca anche con la versione riscritta, come classifiche in più con peso minore; la domanda scritta resta la principale.',
    'cerca_identificatori': 'Parole rare della domanda (codici, pratiche, nomi): i brani che le contengono esattamente vengono prima, con peso doppio nella fusione.',
    'riscrivi_domanda': 'Domanda di seguito ("e lui?"): resa autonoma con i nomi degli scambi precedenti, poi usata per pertinenza, ricerca e risposta.',
    'fondi_risultati': 'Unisce le classifiche (Reciprocal Rank Fusion) e sceglie i brani migliori tra i documenti pertinenti.',
    'pertinenza': 'Clustering (k-means sugli embedding) che separa i documenti pertinenti all\'indagine da quelli estranei.',
    'genera_risposta': 'Il modello linguistico scrive la risposta usando solo i brani scelti.',
    'estrai_persone': 'Riconosce le persone citate nella risposta e le collega ai documenti.',
    'estrai_elementi': 'Dopo la risposta (fuori dal tempo misurato) estrae luoghi, indizi ed eventi e controlla le citazioni.',
    'carica_file': 'Legge i file aggiunti con il pulsante + e li aggiunge all\'indice.',
}
