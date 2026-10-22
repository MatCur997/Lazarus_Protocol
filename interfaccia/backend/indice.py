"""Documenti, brani, ricerca (BM25 + embedding) e indice delle persone. Tutto in memoria."""
import hashlib
import html
import io
import json
import math
import re
import threading
import time
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

import config as cfg
from config import (CARTELLA_CARICATI, CARTELLA_REGISTRO, ESTENSIONI_MARKUP, ESTENSIONI_OK, ESTENSIONI_TESTO, PROGETTO,
                    SOGLIA_BRANI, SOGLIA_CARATTERI, TOP_K)
from modello import embed
from pertinenza import pertinenti as raggruppa_pertinenti
from testo import STOP, estrai_nomi, frasi, ms, parole, prova_persona, senza_accenti
from scenario import RE_CODICE, RE_GIORNO_CARTELLA, RE_GIORNO_TESTO, RE_GIORNO_ID, RE_ID_DOCUMENTO

try:
    import numpy as np
except Exception:    # numpy assente: la similarità si calcola in Python puro (funziona, ma ~20 volte più lenta)
    np = None




def giorno_di(percorso, testo, doc_id, interno=None):
    """In quale giorno dell'indagine è arrivato il documento: dal nome delle cartelle (indagini organizzate per giorno),
    altrimenti dal testo ("giorno operativo 02") o dall'ID (G2-01). None se non si sa."""
    for parte in reversed(Path(percorso).parts[:-1]):
        m = RE_GIORNO_CARTELLA.search(parte)
        if m:
            return int(m[1])
    m = RE_GIORNO_TESTO.search(testo[:1500]) or RE_GIORNO_ID.match(interno or '') or RE_GIORNO_ID.match(doc_id)
    return int(m[1]) if m else None


def profilo_del_caso():
    """Il testo che descrive l'indagine: il suo vettore indica quale cluster è il caso. È `PROFILO_DEL_CASO` di lazarus.py (blocco SCENARIO); solo
    se la squadra ha messo istruzioni proprie in istruzioni.txt si prende la parte prima di «COME RAGIONI» di quel testo."""
    istruzioni = cfg.istruzioni_correnti()
    if istruzioni == cfg.LAZARUS.ISTRUZIONI:
        return cfg.LAZARUS.PROFILO_DEL_CASO
    return istruzioni.split('COME RAGIONI')[0].strip() or istruzioni

# --------------------------------------------------------------------- documenti
def leggi_file(percorso: Path):
    """Ritorna il testo di un file supportato (o None)."""
    est = percorso.suffix.lower()
    dati = percorso.read_bytes()
    return testo_da_bytes(percorso.name, dati) if est != '.zip' else None


def decodifica(dati):
    """Testo del file con i fine riga uniformati: lo stesso documento salvato su Windows (\\r\\n) o altrove (\\n)
    deve arrivare al modello identico, altrimenti cambiano brani, embedding e risposte."""
    for codifica in ('utf-8-sig', 'cp1252', 'latin-1'):
        try:
            testo = dati.decode(codifica)
            break
        except UnicodeDecodeError:
            pass
    else:
        testo = dati.decode('utf-8', errors='replace')
    return testo.replace('\r\n', '\n').replace('\r', '\n')


def ordine_naturale(doc_id):
    """G1-02 prima di G1-10, G2 dopo G1: l'ordine dei documenti non dipende dalla cartella da cui arrivano."""
    return [int(p) if p.isdigit() else p for p in re.split(r'(\d+)', doc_id.lower())]


def da_markup(testo):
    """XML/HTML -> testo. Gli elementi semplici diventano righe "nome: valore" (<title>Equatore</title> ->
    "title: Equatore"), come nei .txt: così titolo, ID e "source:" si riconoscono allo stesso modo."""
    testo = re.sub(r'(?is)<(script|style)\b.*?</\1>|<!--.*?-->|<\?.*?\?>|<!doctype[^>]*>', ' ', testo)
    testo = re.sub(r'<([A-Za-z][\w.-]*)(?:\s[^>]*)?>([^<]*)</\1>',
                   lambda m: m[0] if m[1].lower() in TAG_IN_LINEA else f'\n{m[1]}: {m[2]}\n', testo)
    testo = re.sub(r'(?i)<br\s*/?>|</(p|div|li|tr|h[1-6]|section|article|table|ul|ol)>', '\n', testo)
    testo = html.unescape(re.sub(r'<[^>]+>', '', testo))
    righe = (re.sub(r'[ \t\xa0]+', ' ', r).strip() for r in testo.splitlines())
    return '\n'.join(r for r in righe if r)


TAG_IN_LINEA = {'b', 'strong', 'i', 'em', 'span', 'a', 'u', 'p', 'li', 'td', 'th', 'div', 'small', 'code'}


def da_xlsx(dati):
    """Fogli Excel -> testo: una riga per riga del foglio, celle separate da " | ". Solo libreria standard."""
    def testi(xml):     # testo di <t> anche spezzato in più pezzi (<r><t>..</t></r>)
        return html.unescape(''.join(re.findall(r'<t(?:\s[^>]*)?>([^<]*)</t>', xml)))
    with zipfile.ZipFile(io.BytesIO(dati)) as z:
        nomi = z.namelist()
        # alcuni programmi scrivono i tag con un prefisso (<x:row>, <x:c>): si tolgono per leggere tutti allo stesso modo
        leggi = lambda n: re.sub(r'<(/?)[A-Za-z][\w.-]*:', r'<\1', z.read(n).decode('utf-8'))
        condivisi = []
        if 'xl/sharedStrings.xml' in nomi:
            condivisi = [testi(si) for si in re.findall(r'<si>(.*?)</si>', leggi('xl/sharedStrings.xml'), re.S)]
        titoli = []
        if 'xl/workbook.xml' in nomi:
            titoli = re.findall(r'<sheet\s[^>]*name="([^"]*)"', leggi('xl/workbook.xml'))
        fogli = sorted((n for n in nomi if re.fullmatch(r'xl/worksheets/sheet\d+\.xml', n)),
                       key=lambda n: int(re.search(r'\d+', n.rsplit('/', 1)[1])[0]))
        parti = []
        for i, foglio in enumerate(fogli):
            righe = []
            for riga in re.findall(r'<row\b[^>]*>(.*?)</row>', leggi(foglio), re.S):
                celle = []
                for attr, corpo in re.findall(r'<c\b([^>]*?)(?:/>|>(.*?)</c>)', riga, re.S):
                    tipo = re.search(r'\bt="(\w+)"', attr)
                    valore = re.search(r'<v>([^<]*)</v>', corpo or '')
                    if tipo and tipo[1] == 's' and valore:
                        n = int(valore[1])
                        celle.append(condivisi[n] if n < len(condivisi) else '')
                    elif tipo and tipo[1] == 'inlineStr':
                        celle.append(testi(corpo))
                    else:
                        celle.append(html.unescape(valore[1]) if valore else '')
                if any(c.strip() for c in celle):
                    righe.append(' | '.join(c.strip() for c in celle))
            if righe:
                testata = f'Foglio: {html.unescape(titoli[i])}\n' if i < len(titoli) else ''
                parti.append(testata + '\n'.join(righe))
    return '\n\n'.join(parti)


def testo_da_bytes(nome, dati):
    est = Path(nome).suffix.lower()
    if est in ESTENSIONI_TESTO:
        return decodifica(dati)
    if est in ESTENSIONI_MARKUP:
        return da_markup(decodifica(dati))
    if est == '.xlsx':
        return da_xlsx(dati)
    if est == '.docx':
        with zipfile.ZipFile(io.BytesIO(dati)) as z:
            xml = z.read('word/document.xml').decode('utf-8')
        xml = re.sub(r'</w:p>', '\n', xml)
        return re.sub(r'<[^>]+>', '', xml)
    if est == '.pdf':
        try:
            from pypdf import PdfReader
        except ImportError:
            raise ValueError('Per i PDF installa pypdf (pip install pypdf), oppure converti il file in .txt')
        return '\n'.join(p.extract_text() or '' for p in PdfReader(io.BytesIO(dati)).pages)
    raise ValueError(f'Formato non supportato: {est}')


# ID scritto nel documento: "ID G1-01", "Documento: LX4-1418", "id: ARCH-6408", {"id": "ARCH-6408", ...}
RE_ID_INTERNO = re.compile(r'''^\s*\{?\s*["']?(?:id|documento)["']?\s*:?\s*["']?([A-Z0-9][\w.-]{1,40})''', re.I | re.M)


def id_interno(testo):
    """L'ID con cui il documento si presenta e con cui il modello lo cita ([ARCH-6408] per documento_0421.json)."""
    m = RE_ID_INTERNO.search(testo[:600])
    return m[1] if m else None


RE_TITOLO = re.compile(r'''^\s*title\s*:\s*(.+?)\s*$|["']title["']\s*:\s*"((?:[^"\\]|\\.)+)"''', re.I | re.M)


def titolo_di(doc_id, testo):
    """Il campo title se c'è ("title: Febbraio" nei txt/xml/html, "title": "Febbraio" nei json),
    altrimenti la prima riga che non sia l'ID ("ID G1-01", "id: ...", "Documento: ...")."""
    m = RE_TITOLO.search(testo[:3000])
    if m:
        return (m[1] or json.loads(f'"{m[2]}"')).strip()
    for r in testo.strip().splitlines():
        r = r.strip()
        if r and not re.match(r'(?i)(id|documento)\b\s*:?', r):
            return r[:120]
    return doc_id


def spezza(doc_id, testo, max_car=900):
    """Divide un documento in brani di circa max_car caratteri, rispettando i paragrafi."""
    titolo = titolo_di(doc_id, testo)
    paragrafi = [p.strip() for p in re.split(r'\n\s*\n', testo) if p.strip()]
    pezzi, corrente = [], ''
    for p in paragrafi:
        if len(p) > max_car:
            for f in frasi(p):
                if len(corrente) + len(f) > max_car and corrente:
                    pezzi.append(corrente)
                    corrente = ''
                corrente = (corrente + ' ' + f).strip()
            continue
        if len(corrente) + len(p) > max_car and corrente:
            pezzi.append(corrente)
            corrente = ''
        corrente = (corrente + '\n\n' + p).strip()
    if corrente:
        pezzi.append(corrente)
    return titolo, pezzi


class Indice:
    """Documenti, brani, BM25, embedding e indice delle persone. Tutto in memoria."""

    def __init__(self, embedding_attivo=True):
        self.lock = threading.RLock()
        self.documenti = {}      # id -> {id, nome, origine, titolo, testo}
        self.brani = []          # {doc, n, testo, parole, emb}
        self.df_caso = None      # (frequenze delle parole nei soli brani del caso, quanti sono, lunghezza di `brani`): vedi `frequenze_del_caso`
        self.persone = {}        # nome -> [{doc, frase, contesto}]
        self.alias = {}          # alias -> nome
        self.embedding_attivo = embedding_attivo
        self.embedding_ok = False              # TUTTI i brani hanno l'embedding: serve alla pertinenza (clustering)
        self.embedding_usabile = False         # almeno una parte dei brani ce l'ha: la ricerca per significato lavora su quelli
        self.matrice = None                    # (brani x dimensioni) con righe normalizzate, nell'ordine di self.brani (solo con numpy)
        self.emb_presenti = []                 # per ogni brano: ha l'embedding?
        self.blocco_embedding = threading.Lock()   # un calcolo di embedding alla volta (avvio e caricamento file possono sovrapporsi)
        self.info_pertinenza = {'dettaglio': 'non ancora calcolata: tutti pertinenti', 'pertinenti': None, 'esclusi': 0}
        self.mappa_pertinenza = None
        self._letture = {}       # percorso -> ((data di modifica, dimensione), testo): vedi `_leggi_con_cache`
        self._basi = {}          # id del documento -> (testo, titolo, brani già analizzati): vedi `_costruisci`
        self.postings = {}       # parola -> [(indice del brano, frequenza)]: BM25 tocca solo i brani che contengono le parole della domanda
        self.indice_codici = {}  # codice -> [indici dei brani che lo contengono] (tutto l'archivio): ricerca a salti e recupero dei documenti esclusi
        self._nomi_ordinati = None  # nomi di persona e alias dal più lungo, per `persone_in` (si rifà quando cambia l'elenco delle persone)
        self.cache_emb = {}
        # Con numpy la cache è una matrice .npy (float32) + l'elenco delle chiavi: si legge in decimi di secondo invece di decine di secondi
        # (il vecchio JSON pesava ~66 MB) e i vettori restano righe di un solo array, non milioni di float Python. Senza numpy, o se
        # i vettori hanno dimensioni diverse, si usa ancora il JSON. Un JSON vecchio si legge e viene convertito al primo salvataggio.
        self.file_cache_json = CARTELLA_REGISTRO / 'cache_embedding.json'
        self.file_cache_npy = CARTELLA_REGISTRO / 'cache_embedding.npy'
        self.file_cache_chiavi = CARTELLA_REGISTRO / 'cache_embedding_chiavi.json'
        self.file_cache = self.file_cache_npy if np is not None else self.file_cache_json
        self.file_esclusi = CARTELLA_REGISTRO / 'non_persone.txt'
        self.esclusi = set()
        self.cache_emb = self._leggi_cache()
        if np is not None and self.cache_emb and not self.file_cache_npy.exists():
            self._salva_cache()         # cache JSON di una versione precedente: si converte subito, non al prossimo documento nuovo
        if self.file_esclusi.exists():
            self.esclusi = {r.strip() for r in self.file_esclusi.read_text(encoding='utf-8').splitlines() if r.strip()}
        # correzioni a mano del segnale "sospetto": nome del file -> true/false (valgono anche nelle sessioni dopo)
        self.file_sospetti = CARTELLA_REGISTRO / 'sospetti.json'
        try:
            self.sospetti_manuali = json.loads(self.file_sospetti.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            self.sospetti_manuali = {}

    def _segna_sospetto(self, d):
        """Metadato "sospetto" del documento: nessun riconoscimento automatico (niente regole sulle frasi dei documenti), solo la
        segnalazione a mano del team (modalità debug)."""
        manuale = self.sospetti_manuali.get(d['nome'])
        d['sospetto'] = 'segnato a mano come sospetto' if manuale is True else None
        d['sospetto_manuale'] = manuale

    def imposta_sospetto(self, doc_id, valore):
        """valore: True/False per correggere a mano, None per tornare al riconoscimento automatico."""
        with self.lock:
            d = self.documenti.get(doc_id)
            if not d:
                raise ValueError('Documento non trovato')
            if valore is None:
                self.sospetti_manuali.pop(d['nome'], None)
            else:
                self.sospetti_manuali[d['nome']] = bool(valore)
            CARTELLA_REGISTRO.mkdir(exist_ok=True)
            self.file_sospetti.write_text(json.dumps(self.sospetti_manuali, ensure_ascii=False, indent=1), encoding='utf-8')
            self._segna_sospetto(d)
            for b in self.brani:
                if b['doc'] == doc_id:
                    b['sospetto'] = d['sospetto']
            return d['sospetto']

    # -- caricamento
    def ricarica(self):
        docs, letture = {}, {}
        dati = Path(cfg.CARTELLA_DATI).resolve() if cfg.CARTELLA_DATI else None    # di solito nessuna (solo --dati)
        for cartella, origine in ((dati, 'squadra'), (CARTELLA_CARICATI, 'caricato')):
            if not cartella or not cartella.exists():
                continue
            for p in sorted(cartella.rglob('*')):
                if origine == 'caricato' and dati and p.resolve().is_relative_to(dati):
                    continue    # la cartella dati della squadra può stare dentro caricati/: si legge una volta sola
                if p.name == Path(cfg.DIARIO_NOTEBOOK).name:
                    continue    # il diario delle risposte non è un documento: il modello citerebbe se stesso
                if p.is_file() and p.suffix.lower() in ESTENSIONI_OK - {'.zip'}:
                    try:
                        testo = self._leggi_con_cache(p, letture)
                    except Exception as e:  # file illeggibile: lo segnaliamo ma andiamo avanti
                        print(f'  ! {p.name}: {e}')
                        continue
                    doc_id = p.stem if p.stem not in docs else f'{p.stem}_{origine}'
                    # consultazione = NON pertinente all'indagine: lo decide il clustering (calcola_pertinenza) quando ci sono gli embedding.
                    # Un documento già visto con lo stesso testo tiene l'esito del clustering precedente finché il nuovo calcolo (in background)
                    # non finisce: altrimenti, dopo ogni caricamento, per qualche secondo tutte le voci estranee tornerebbero «del caso».
                    prima = self.documenti.get(doc_id)
                    stesso = bool(prima) and prima['testo'] == testo
                    docs[doc_id] = {'id': doc_id, 'nome': p.name, 'origine': origine,
                                    'percorso': str(p.relative_to(PROGETTO)) if p.is_relative_to(PROGETTO) else str(p),
                                    'testo': testo, 'consultazione': prima['consultazione'] if stesso else False,
                                    'pertinenza': prima.get('pertinenza') if stesso else None, 'id_interno': id_interno(testo)}
                    rel = p.relative_to(cartella) if p.is_relative_to(cartella) else p
                    docs[doc_id]['giorno'] = giorno_di(rel, testo, doc_id, docs[doc_id]['id_interno'])
        self._letture = letture         # i file spariti escono dalla cache
        docs = dict(sorted(docs.items(), key=lambda kv: ordine_naturale(kv[0])))   # stesso ordine da qualunque cartella
        for d in docs.values():
            self._segna_sospetto(d)
        with self.lock:
            self.documenti = docs
            self._costruisci()

    def _leggi_con_cache(self, p, letture):
        """Testo del file, riletto (e ri-analizzato: PDF, xlsx, docx) solo se è cambiato dall'ultima volta (data di modifica e dimensione).
        Un caricamento o una rimozione non rilegge tutto l'archivio."""
        st = p.stat()
        firma = (st.st_mtime_ns, st.st_size)
        vecchia = self._letture.get(str(p))
        testo = vecchia[1] if vecchia and vecchia[0] == firma else leggi_file(p)
        letture[str(p)] = (firma, testo)
        return testo

    def _costruisci(self):
        brani, basi = [], {}
        for d in self.documenti.values():
            # l'analisi dei brani (parole, frequenze, coppie, codici) di un documento con lo stesso testo si riusa: un caricamento non la rifà per tutti
            vecchia = self._basi.get(d['id'])
            if vecchia and vecchia[0] == d['testo']:
                titolo, pezzi = vecchia[1], vecchia[2]
            else:
                titolo, testi = spezza(d['id'], d['testo'])
                pezzi = []
                for n, t in enumerate(testi):
                    # L'embedding resta sul solo testo del brano. Provato con titolo + ultima frase del brano precedente davanti (dal secondo
                    # brano in poi): i vettori medi delle voci lunghe cambiano e il clustering di pertinenza crolla (1523 documenti: del caso
                    # presi 1/518 invece di 518/518). Il titolo dei brani successivi lo riceve solo il modello, nel prompt (contesto_da_fonti).
                    p = parole(f'{titolo} {t}')
                    pezzi.append({'n': n, 'testo': t, 'parole': p, 'freq': Counter(p), 'frasi': coppie_di(f'{titolo} {t}'),
                                  'codici': codici_del_testo(t)})
            basi[d['id']] = (d['testo'], titolo, pezzi)
            d['titolo'] = titolo
            for base in pezzi:
                brani.append({**base, 'doc': d['id'], 'id_interno': d.get('id_interno'), 'titolo': titolo, 'consultazione': d.get('consultazione', False),
                              'sospetto': d.get('sospetto'), 'giorno': d.get('giorno'), 'emb': None})
        self._basi = basi
        # BM25 con indice invertito: per ogni parola i brani che la contengono (la ricerca non scorre tutto l'archivio)
        self.df = Counter()
        postings, codici = defaultdict(list), defaultdict(list)
        for i, b in enumerate(brani):
            for w, f in b['freq'].items():
                postings[w].append((i, f))
            for c in b['codici']:
                codici[c].append(i)
        self.df = Counter({w: len(v) for w, v in postings.items()})
        self.postings, self.indice_codici = dict(postings), dict(codici)
        self.lung_media = (sum(len(b['parole']) for b in brani) / len(brani)) if brani else 1
        self.df_caso = None             # si ricalcola alla prima ricerca (vedi `frequenze_del_caso`)
        self.brani = brani
        for b in brani:         # embedding già calcolati (cache): la ricerca per significato non aspetta il nuovo calcolo
            b['emb'] = self.cache_emb.get(self._chiave(b['testo'])) if self.embedding_attivo else None
        self._carica_matrice()
        # Le persone si cercano solo nei documenti pertinenti. Se arriveranno gli embedding, la pertinenza la decide il
        # clustering (calcola_pertinenza), che poi ricostruisce l'elenco: farlo qui su tutto l'archivio costerebbe
        # ~17 s con 1000 voci estranee, buttati subito dopo. Senza embedding (pochi testi, demo) si fa subito.
        if self.embedding_attivo and not tutto_nel_contesto(brani):
            self.persone, self.alias, self._nomi_ordinati = {}, {}, None
        else:
            self._indicizza_persone()

    def _indicizza_persone(self):
        persone = defaultdict(list)
        for d in self.documenti.values():
            if d.get('consultazione'):      # fonti esterne: cercabili, ma non sono il caso
                continue
            elenco = frasi(d['testo'])
            for i, f in enumerate(elenco):
                for nome in set(estrai_nomi(f)):
                    if nome in self.esclusi:
                        continue
                    contesto = ' '.join(elenco[max(0, i - 1):i + 2])
                    persone[nome].append({'doc': d['id'], 'frase': f, 'contesto': contesto})
        # i nomi singoli che coincidono con nome o cognome di una persona già nota diventano alias
        multipli = [n for n in persone if ' ' in n]
        alias = {}
        for n in list(persone):
            if ' ' in n:
                continue
            corrispondenti = [m for m in multipli if n in m.split()]
            if len(corrispondenti) == 1:
                persone[corrispondenti[0]].extend(persone.pop(n))
                alias[n] = corrispondenti[0]
        # restano solo i nomi con almeno una prova di essere una persona: "Officina Sasso" o "Presso Ripetitore Rame" non ne hanno
        persone = {n: v for n, v in persone.items() if any(prova_persona(x['frase'], n) for x in v)
                   or any(prova_persona(x['frase'], a) for a, vero in alias.items() if vero == n for x in v)}
        alias = {a: vero for a, vero in alias.items() if vero in persone}
        multipli = [n for n in multipli if n in persone]
        for n in multipli:
            for t in n.split():
                if sum(t in m.split() for m in multipli) == 1 and t not in persone:
                    alias.setdefault(t, n)
        self.persone = dict(persone)
        self.alias = alias
        self._nomi_ordinati = None

    # -- embedding
    GRUPPO_EMBEDDING = 32       # brani per richiesta a Ollama
    SALVA_OGNI = 40             # gruppi tra un salvataggio della cache e il successivo (~1300 brani): un'interruzione non butta il lavoro fatto

    def calcola_embedding(self):
        if not self.embedding_attivo or tutto_nel_contesto(self.brani):
            return          # pochi testi: gli embedding non servono e farebbero caricare un secondo modello
        with self.blocco_embedding:
            with self.lock:
                mancanti = [b for b in self.brani if self._chiave(b['testo']) not in self.cache_emb]
            errore = None
            try:
                for k, i in enumerate(range(0, len(mancanti), self.GRUPPO_EMBEDDING), 1):
                    gruppo = mancanti[i:i + self.GRUPPO_EMBEDDING]
                    vettori = embed([b['testo'] for b in gruppo])
                    for b, v in zip(gruppo, vettori):
                        self.cache_emb[self._chiave(b['testo'])] = v
                    if k % self.SALVA_OGNI == 0:
                        self._salva_cache()
            except Exception as e:
                errore = e
            with self.lock:
                for b in self.brani:
                    b['emb'] = self.cache_emb.get(self._chiave(b['testo']))
                self._carica_matrice()
            if mancanti:
                self._salva_cache()           # anche dopo un errore: gli embedding già calcolati non si rifanno
            if errore:
                pronti = sum(self.emb_presenti)
                print(f'  Embedding non completi ({errore}): pronti {pronti} brani su {len(self.brani)}. '
                      + ('La ricerca per significato usa quelli pronti, con le parole chiave.' if pronti else 'Uso solo la ricerca per parole chiave.'))
            self.calcola_pertinenza()

    def aggiorna_in_background(self):
        """Embedding dei brani nuovi e pertinenza in un thread: chi carica o rimuove un file riceve subito la risposta. Nel frattempo la ricerca
        usa gli embedding già pronti e, per i documenti già visti, l'esito del clustering precedente."""
        threading.Thread(target=self.calcola_embedding, daemon=True).start()

    def _leggi_cache(self):
        """{chiave: vettore} dalla cache su disco: .npy se c'è (e numpy), altrimenti il JSON."""
        if np is not None and self.file_cache_npy.exists() and self.file_cache_chiavi.exists():
            try:
                chiavi = json.loads(self.file_cache_chiavi.read_text(encoding='utf-8'))
                matrice = np.load(self.file_cache_npy, allow_pickle=False)
                if matrice.ndim == 2 and len(chiavi) == len(matrice):
                    return {k: matrice[i] for i, k in enumerate(chiavi)}
            except (OSError, ValueError):
                pass                    # file rovinato (spegnimento a metà scrittura): si ricade sul JSON o si ricalcola
        if self.file_cache_json.exists():
            try:
                return json.loads(self.file_cache_json.read_text(encoding='utf-8'))
            except ValueError:
                pass
        return {}

    def _salva_cache(self):
        CARTELLA_REGISTRO.mkdir(exist_ok=True)
        if np is not None and self.cache_emb:
            try:
                chiavi = list(self.cache_emb)
                matrice = np.array([self.cache_emb[k] for k in chiavi], dtype=np.float32)
                if matrice.ndim == 2:
                    # prima i file temporanei, poi la sostituzione: un'interruzione non lascia una cache a metà
                    tmp_npy, tmp_chiavi = self.file_cache_npy.with_suffix('.tmp'), self.file_cache_chiavi.with_suffix('.tmp')
                    with open(tmp_npy, 'wb') as f:
                        np.save(f, matrice, allow_pickle=False)
                    tmp_chiavi.write_text(json.dumps(chiavi), encoding='utf-8')
                    tmp_npy.replace(self.file_cache_npy)
                    tmp_chiavi.replace(self.file_cache_chiavi)
                    return
            except ValueError:
                pass                    # vettori di dimensioni diverse (cambio del modello di embedding): resta il JSON
        self.file_cache_json.write_text(
            json.dumps({k: (v.tolist() if hasattr(v, 'tolist') else v) for k, v in self.cache_emb.items()}), encoding='utf-8')

    def _carica_matrice(self):
        """Stato degli embedding dei brani attuali: `embedding_ok` = tutti pronti, `embedding_usabile` = almeno uno. Con numpy costruisce
        la matrice normalizzata: la similarità con la domanda diventa un solo prodotto (~20 ms su 5000 brani invece di ~500 ms in Python)."""
        self.emb_presenti = [b.get('emb') is not None for b in self.brani]
        self.embedding_ok = bool(self.brani) and all(self.emb_presenti)
        self.embedding_usabile = any(self.emb_presenti)
        self.matrice = None
        if np is not None and self.embedding_usabile:
            dimensioni = len(next(b['emb'] for b in self.brani if b.get('emb') is not None))
            m = np.zeros((len(self.brani), dimensioni), dtype=np.float32)
            for i, b in enumerate(self.brani):
                if b.get('emb') is not None:
                    m[i] = b['emb']
            norme = np.linalg.norm(m, axis=1, keepdims=True)
            norme[norme == 0] = 1.0
            self.matrice = m / norme

    @staticmethod
    def classifica_per_significato(vettore, brani, matrice, presenti):
        """(indici dei brani dal più al meno simile al vettore, similarità nello stesso ordine). Solo i brani che hanno l'embedding."""
        if matrice is not None and len(matrice) == len(brani):
            v = np.asarray(vettore, dtype=np.float32)
            norma = float(np.linalg.norm(v))
            s = matrice @ (v / norma if norma else v)
            ordine = [i for i in np.argsort(-s, kind='stable').tolist() if presenti[i]]
            return ordine, [float(s[i]) for i in ordine]
        coppie = sorted(((coseno(vettore, b['emb']), i) for i, b in enumerate(brani) if presenti[i] and b.get('emb') is not None), reverse=True)
        return [i for _, i in coppie], [s for s, _ in coppie]

    # -- pertinenza (clustering, vedi pertinenza.py)
    def calcola_pertinenza(self):
        """Segna come non pertinenti ("consultazione") i documenti fuori dal cluster dell'indagine.
        Senza embedding (pochi testi, demo, Ollama spento) restano tutti pertinenti."""
        t = time.perf_counter()
        with self.lock:
            documenti = dict(self.documenti)
            brani = list(self.brani)
        vettori = {}
        if self.embedding_ok:
            for b in brani:
                vettori.setdefault(b['doc'], []).append(b['emb'])
        mappa = None
        if not vettori:
            presi, somiglianza = set(documenti), {}
            dettaglio = f'{len(documenti)} documenti: senza embedding, tutti pertinenti'
        else:
            try:
                profilo = embed([profilo_del_caso()])[0]
                if np is not None:       # i vettori della cache .npy sono array: la media si fa in numpy, non colonna per colonna
                    medi = {d: np.mean(np.asarray(v, dtype=np.float64), axis=0).tolist() for d, v in vettori.items()}
                else:
                    medi = {d: [sum(c) / len(v) for c in zip(*v)] for d, v in vettori.items()}
                presi, somiglianza, dettaglio, mappa = raggruppa_pertinenti(medi, profilo)
            except Exception as e:         # Ollama non risponde: meglio non escludere nulla
                presi, somiglianza, dettaglio = set(documenti), {}, f'non calcolata ({e}): tutti pertinenti'
        with self.lock:
            if set(self.documenti) != set(documenti):
                return                     # nel frattempo sono cambiati i documenti: ci penserà il prossimo calcolo
            for d in self.documenti.values():
                d['consultazione'] = d['id'] not in presi
                d['pertinenza'] = round(somiglianza[d['id']], 3) if d['id'] in somiglianza else None
            for b in self.brani:
                b['consultazione'] = self.documenti[b['doc']]['consultazione']
            self._indicizza_persone()      # i nomi dei documenti non pertinenti non sono persone del caso
            self.info_pertinenza = {'dettaglio': dettaglio, 'ms': ms(t), 'pertinenti': len(presi & set(documenti)),
                                    'esclusi': len(set(documenti) - presi)}
            self.mappa_pertinenza = mappa      # cluster e coordinate 2D per la RAG view (None se non c'è clustering)
            self.df_caso = None                # i brani del caso sono cambiati: le frequenze per gli identificatori si rifanno
        print(f'  Pertinenza: {dettaglio} ({ms(t):.0f} ms)')

    @staticmethod
    def _chiave(testo):
        return hashlib.sha1(f'{cfg.MODELLO_EMBEDDING}|{testo}'.encode()).hexdigest()

    def frequenze_del_caso(self, brani):
        """(in quanti brani del caso compare ogni parola, quanti sono i brani del caso). Gli identificatori si cercano tra i soli brani del caso:
        contando anche i documenti estranei, una parola comune nel caso («affidabile») compare per caso in pochi brani estranei e sembra rara."""
        if self.df_caso is None or self.df_caso[2] != len(brani):
            df, df_frasi = Counter(), Counter()
            n_caso = 0
            for b in brani:
                if not b['consultazione']:
                    df.update(set(b['parole']))
                    df_frasi.update(frasi_di(b))
                    n_caso += 1
            self.df_caso = (df, n_caso, len(brani), df_frasi)
        return self.df_caso[0], self.df_caso[1]

    def frequenze_frasi_del_caso(self, brani):
        """In quanti brani del caso compare ogni coppia di parole adiacenti (vedi `coppie_di`): «supporto vuoto» può stare in un brano solo anche se
        «supporto» e «vuoto», da soli, sono in decine."""
        self.frequenze_del_caso(brani)
        return self.df_caso[3]

    # -- ricerca
    def cerca(self, domanda, strumenti, ipotesi=None, traccia=None, riscritta=None):
        """Brani per la domanda: parole chiave (BM25) + significato (embedding) + HyDE se serve, fusi con RRF.
        `ipotesi`: funzione senza argomenti che ritorna la risposta ipotetica del modello (HyDE), o None.
        `riscritta`: la stessa domanda resa autonoma (seguiti): si cerca anche con quella, come classifiche in più con peso minore. La domanda
        scritta resta la principale: la riscrittura può aggiungere dettagli sbagliati e non deve far perdere le parole dell'utente."""
        with self.lock:
            brani = list(self.brani)
            n = len(brani)
            matrice, presenti, usabile = self.matrice, list(self.emb_presenti), self.embedding_usabile
            # df, postings e lunghezza media dello STESSO archivio dei brani: una ricostruzione a metà ricerca non li mescola
            df, postings, lung_media, indice_codici = self.df, self.postings, self.lung_media, self.indice_codici
        if not n:
            return []
        if tutto_nel_contesto(brani):
            # come lazarus.py: pochi testi -> si danno tutti al modello, niente ricerca né embedding
            strumenti.append({'nome': 'fondi_risultati', 'ms': 0, 'dettaglio': f'{n} brani: documenti passati interi'})
            if traccia is not None:
                traccia.update({'brani': n, 'pertinenti': n, 'tutti_pertinenti': True, 'tutto_archivio': True, 'top_k': TOP_K,
                                'bm25': [], 'semantica': [], 'hyde': [], 'rrf': []})
            return [fonte_da_brano(b) for b in brani]
        t = time.perf_counter()
        q = parole(domanda)

        def punteggi_bm25(parole_query):
            """[(punteggio, indice)] dei soli brani con almeno una parola della domanda: l'indice invertito evita di scorrere tutto l'archivio."""
            punti = defaultdict(float)
            for w in parole_query:          # una parola ripetuta nella domanda conta più volte, come prima
                if w not in postings:
                    continue
                idf = math.log(1 + (n - df[w] + 0.5) / (df[w] + 0.5))
                for i, f in postings[w]:
                    punti[i] += idf * f * 2.2 / (f + 1.2 * (0.25 + 0.75 * len(brani[i]['parole']) / lung_media))
            return [(s, i) for i, s in punti.items()]
        punteggi = punteggi_bm25(q)
        # classifiche COMPLETE (non solo i primi 30): i documenti pertinenti possono stare in fondo all'archivio intero
        bm25 = [i for s, i in sorted(punteggi, reverse=True) if s > 0]
        strumenti.append({'nome': 'cerca_parole_chiave', 'ms': ms(t), 'dettaglio': f'{len(bm25)} brani con parole in comune'})
        pertinenti = [i for i, b in enumerate(brani) if not b['consultazione']]
        # al massimo MAX_BRANI_PER_DOC brani per documento: i documenti del caso sono brevi (un brano), un documento
        # lungo o un intruso del clustering non deve occupare il posto degli altri
        # «tutti i pertinenti» solo se ci stanno INTERI: tagliarli ai primi MAX_BRANI_PER_DOC brani perdeva il resto dei documenti lunghi senza dirlo
        # (meglio la ricerca normale, che sceglie i brani migliori di ogni documento)
        tutti_pertinenti = bool(pertinenti) and len(pertinenti) < n and tutto_nel_contesto([brani[i] for i in pertinenti])

        semantica, punti_sem, migliore = [], [], None
        if usabile:
            t = time.perf_counter()
            try:
                qv = embed([domanda])[0]
                semantica, punti_sem = self.classifica_per_significato(qv, brani, matrice, presenti)
                migliore = punti_sem[0]
                strumenti.append({'nome': 'cerca_semantica', 'ms': ms(t), 'dettaglio': f'migliore similarità {migliore:.2f}'
                                  + ('' if all(presenti) else f' · embedding pronto su {sum(presenti)}/{n} brani')})
            except Exception as e:
                strumenti.append({'nome': 'cerca_semantica', 'ms': ms(t), 'dettaglio': f'errore: {e}'})

        # Identificatori: parole della domanda presenti in pochissimi brani (codici, pratiche, nomi rari). Chi le contiene esattamente
        # va prima, anche se per significato assomiglia a mille altri brani; a parità, contano il numero di identificatori e il BM25.
        # Si calcolano prima di HyDE: se la domanda ne ha già, HyDE (~2 s) non serve (HYDE_SALTA_CON_IDENTIFICATORI).
        t = time.perf_counter()
        # Le frequenze sono quelle dei soli brani del caso: gli identificatori sono parole rare NEL CASO, e solo brani del caso possono contenerle.
        df_caso, n_caso = self.frequenze_del_caso(brani)
        soglia_df = max(cfg.IDENT_DF_MIN, cfg.IDENT_DF_FRAZIONE * n_caso)
        rari = sorted({w for w in q if 0 < df_caso.get(w, 0) <= soglia_df})
        # Frasi rare: coppie di parole adiacenti della domanda che compaiono in pochissimi brani del caso, anche se le due parole sono comuni.
        frasi_rare = []
        if cfg.IDENT_FRASI:
            df_frasi = self.frequenze_frasi_del_caso(brani)
            frasi_rare = sorted({f for f in coppie_di(domanda) if 0 < df_frasi.get(f, 0) <= cfg.IDENT_FRASE_DF_MAX})
        # Recupero: un codice della domanda (pratica, lotto) che compare in pochi brani, esclusi dal clustering. Il clustering decide per documento
        # e può sbagliare (un documento del caso scritto diversamente dagli altri); un codice esatto è una prova più forte. Quei brani, per questa
        # domanda, valgono come brani del caso (non diventano però indizi: l'esclusione nell'indice resta).
        codici_domanda = [c for c in codici_del_testo(domanda.upper()) if 0 < len(indice_codici.get(c, ())) <= soglia_df]
        recuperati = {i for c in codici_domanda for i in indice_codici[c] if brani[i]['consultazione']} if cfg.RECUPERA_CON_CODICI else set()
        identificatori = []
        if rari or frasi_rare or recuperati:
            trovati = [(sum(w in brani[i]['freq'] for w in rari) + sum(f in frasi_di(brani[i]) for f in frasi_rare)
                        + sum(c in brani[i].get('codici', ()) for c in codici_domanda), s, i)
                       for s, i in punteggi if s > 0 and (not brani[i]['consultazione'] or i in recuperati)]
            identificatori = [i for k, s, i in sorted((x for x in trovati if x[0]), reverse=True)[:cfg.IDENT_MAX_BRANI]]
            strumenti.append({'nome': 'cerca_identificatori', 'ms': ms(t),
                              'dettaglio': f'{len(identificatori)} brani con {", ".join(rari + [f"«{a} {b}»" for a, b in frasi_rare])}'
                                           + (f' · {len(recuperati)} brani recuperati tra gli esclusi grazie a {", ".join(codici_domanda)}' if recuperati else '')})

        # HyDE: solo se la domanda da sola trova poco. È una classifica IN PIÙ: un'ipotesi sbagliata
        # non può togliere i brani trovati con la domanda vera.
        hyde = []
        if ipotesi and migliore is not None:
            t = time.perf_counter()
            if tutti_pertinenti:
                strumenti.append({'nome': 'cerca_hyde', 'ms': 0, 'dettaglio': 'non serve: il modello riceve tutti i documenti pertinenti'})
            elif rari and identificatori and cfg.HYDE_SALTA_CON_IDENTIFICATORI:      # solo le parole rare saltano HyDE, non le frasi rare
                strumenti.append({'nome': 'cerca_hyde', 'ms': 0, 'dettaglio': f'non serve: la domanda ha già {len(identificatori)} brani con {", ".join(rari)}'})
            elif migliore >= cfg.HYDE_SOGLIA:
                strumenti.append({'nome': 'cerca_hyde', 'ms': 0, 'dettaglio': f'non serve: similarità {migliore:.2f} ≥ {cfg.HYDE_SOGLIA:.2f}'})
            else:
                try:
                    testo_ipotesi = ipotesi()
                    hv = embed([testo_ipotesi])[0]
                    hyde, punti_h = self.classifica_per_significato(hv, brani, matrice, presenti)
                    strumenti.append({'nome': 'cerca_hyde', 'ms': ms(t), 'testo': testo_ipotesi,
                                      'dettaglio': f'risposta ipotetica di {len(testo_ipotesi.split())} parole · migliore similarità {punti_h[0]:.2f}'})
                except Exception as e:
                    strumenti.append({'nome': 'cerca_hyde', 'ms': ms(t), 'dettaglio': f'errore: {e}'})

        # Domanda riscritta (seguiti): classifiche in più, con peso minore (cfg.PESI_RRF)
        extra = {}
        if riscritta and _chiave_testo(riscritta) != _chiave_testo(domanda):
            t = time.perf_counter()
            extra['riscritta_bm25'] = [i for s, i in sorted(punteggi_bm25(parole(riscritta)), reverse=True) if s > 0]
            if usabile:
                try:
                    extra['riscritta_semantica'] = self.classifica_per_significato(embed([riscritta])[0], brani, matrice, presenti)[0]
                except Exception as e:
                    strumenti.append({'nome': 'cerca_riscritta', 'ms': ms(t), 'dettaglio': f'errore nel significato: {e}'})
            strumenti.append({'nome': 'cerca_riscritta', 'ms': ms(t), 'dettaglio': f'domanda riscritta cercata insieme a quella scritta: {len(extra)} classifiche in più'})

        t = time.perf_counter()
        classifiche = {'bm25': bm25, 'semantica': semantica, 'hyde': hyde, 'identificatori': identificatori, **extra}

        def fondi(classifiche):           # Reciprocal Rank Fusion pesata (cfg.PESI_RRF)
            punti = defaultdict(float)
            for nome, c in classifiche.items():
                peso = cfg.PESI_RRF.get(nome, 1.0)
                for pos, i in enumerate(c):
                    punti[i] += peso / (60 + pos)
            return punti
        # Su tutto l'archivio, SENZA identificatori: decide se la domanda punta a documenti non pertinenti (vedi sotto). Una parola rara
        # che compare solo in una voce enciclopedica ("affidabile") non deve far passare al modello testo estraneo al caso.
        rrf = fondi({nome: c for nome, c in classifiche.items() if nome != 'identificatori'})
        classifica = sorted(rrf, key=rrf.get, reverse=True)
        # solo i documenti pertinenti all'indagine (protocollo di clustering) danno il loro contenuto al modello:
        # se ci stanno tutti, li riceve tutti (nell'ordine dei documenti); altrimenti i migliori tra i pertinenti
        insieme = set(pertinenti) | recuperati
        punti_scelti = rrf
        if tutti_pertinenti:
            scelti = pertinenti + sorted(recuperati)
        else:
            # tra i pertinenti contano anche gli identificatori: un codice esatto vale più di una somiglianza di significato
            punti_scelti = rrf_p = fondi({nome: [i for i in c if i in insieme] for nome, c in classifiche.items()})
            migliori = sorted(rrf_p, key=rrf_p.get, reverse=True) or pertinenti
            candidati = limita_per_documento(migliori, brani)[:max(TOP_K, cfg.MMR_POOL)]
            candidati, copie_di = raggruppa_copie(candidati, matrice, cfg.COPIE_SOGLIA)   # le varianti quasi identiche non riempiono i posti
            scelti = diversifica(candidati, rrf_p, matrice, cfg.MMR_LAMBDA, TOP_K)
        # se la domanda punta soprattutto a documenti NON pertinenti (es. "cosa dice il file sulle anatre?"),
        # il modello ne riceve anche il contenuto, segnato come non pertinente: può parlarne e citarli,
        # ma non diventano mai indizi, eventi o luoghi (vedi del_caso in server.py e le istruzioni)
        fuori, visti = [], set()
        if tutti_pertinenti:
            copie_di = {}
        # Con molte voci estranee vicine per argomento il primo posto su tutto l'archivio va spesso a una di loro per poco: entrano solo se battono
        # il miglior brano del caso con un margine (FUORI_MARGINE) e al massimo FUORI_MAX_BRANI, così non prendono il posto del caso nel prompt.
        migliore_caso = max((rrf[i] for i in classifica if not brani[i]['consultazione'] or i in recuperati), default=0.0)
        if classifica and brani[classifica[0]]['consultazione'] and classifica[0] not in recuperati \
                and rrf[classifica[0]] >= migliore_caso * cfg.FUORI_MARGINE:
            soglia = rrf[classifica[0]] * 0.8
            for i in classifica:
                if not brani[i]['consultazione'] or i in recuperati or rrf[i] < soglia:
                    continue
                if brani[i]['doc'] not in visti and len(visti) == 3:
                    continue                      # al massimo 3 documenti non pertinenti
                visti.add(brani[i]['doc'])
                fuori.append(fonte_da_brano(brani[i], rrf[i]))
                if len(fuori) == cfg.FUORI_MAX_BRANI:
                    break
        strumenti.append({'nome': 'fondi_risultati', 'ms': ms(t),
                          'dettaglio': (f'tutti i {len(scelti)} brani pertinenti' if tutti_pertinenti else f'{len(scelti)} brani pertinenti scelti')
                          + (f' · {len(fuori)} brani da {len(visti)} documenti non pertinenti, richiesti dalla domanda' if fuori else '')})
        if traccia is not None:     # RAG view: le classifiche con i punteggi, per vedere come si è arrivati alle fonti
            def voce(i, punteggio):
                b = brani[i]
                return {'doc': b['doc'], 'n': b['n'], 'titolo': self.documenti.get(b['doc'], {}).get('titolo', ''),
                        'punteggio': round(punteggio, 4), 'pertinente': not b['consultazione'], 'sospetto': bool(b.get('sospetto'))}
            punti_bm25 = dict((i, s) for s, i in punteggi)
            scelti_set, fuori_set = set(scelti), {(f['doc'], f['n']) for f in fuori}
            traccia.update({
                'brani': n, 'pertinenti': len(pertinenti), 'tutti_pertinenti': tutti_pertinenti, 'top_k': TOP_K,
                'bm25': [voce(i, punti_bm25[i]) for i in bm25[:15]],
                'semantica': [voce(i, s) for i, s in zip(semantica[:15], punti_sem[:15])],
                'hyde': [voce(i, s) for i, s in zip(hyde[:15], punti_h[:15])] if hyde else [],
                'identificatori': [voce(i, punti_bm25[i]) for i in identificatori[:15]], 'rari': rari + [f'{a} {b}' for a, b in frasi_rare],
                'rrf': [{**voce(i, rrf[i]), 'esito': 'scelto' if i in scelti_set else
                         ('usato, non pertinente' if (brani[i]['doc'], brani[i]['n']) in fuori_set else
                          ('non pertinente' if brani[i]['consultazione'] else 'fuori dai primi'))}
                        for i in classifica[:25]]})
        fonti = [fonte_da_brano(brani[i], punti_scelti.get(i, 0)) for i in scelti]
        for i, f in zip(scelti, fonti):
            if i in recuperati:
                f.update(consultazione=False, recuperato=True)      # nel prompt come brano del caso; non diventa un indizio (del_caso)
            if copie_di.get(i):
                f['copie'] = sorted({brani[c]['doc'] for c in copie_di[i]} - {f['doc']})      # ID delle copie quasi identiche, per il prompt
        return fuori + fonti

    def brani_con_nome(self, nome, brani=None):
        """Indici dei brani che contengono il nome come parola intera (maiuscole a parte). Si parte dall'indice invertito (i brani che hanno
        tutte le parole del nome), poi si controlla la sequenza esatta: niente scansione di tutto l'archivio a ogni salto."""
        with self.lock:
            brani = self.brani if brani is None else brani
            postings = self.postings
        chiavi = set(parole(nome))
        if not chiavi or any(w not in postings for w in chiavi):
            return []
        candidati = set.intersection(*({i for i, _ in postings[w]} for w in chiavi))
        esatto = re.compile(rf'(?<![\w]){re.escape(nome)}(?![\w])', re.I)
        return sorted(i for i in candidati if i < len(brani) and esatto.search(brani[i]['testo']))

    def persone_in(self, testo):
        """Persone citate in un testo, solo se compaiono anche nei documenti (nomi inventati o etichette no)."""
        trovate = []
        with self.lock:
            if self._nomi_ordinati is None:     # ordinarli a ogni chiamata costava: la bacheca la chiama per ogni voce
                self._nomi_ordinati = sorted(list(self.persone) + list(self.alias), key=len, reverse=True)
            for nome in self._nomi_ordinati:
                # `nome in testo` scarta in C quasi tutti i nomi: la regex (lenta, una per nome) parte solo per i pochi candidati
                if nome in testo and re.search(rf'(?<![\w]){re.escape(nome)}(?![\w])', testo):
                    vero = self.alias.get(nome, nome)
                    if vero not in trovate and vero not in self.esclusi:
                        trovate.append(vero)
        return trovate

    def escludi(self, nome):
        with self.lock:
            self.esclusi.add(nome)
            CARTELLA_REGISTRO.mkdir(exist_ok=True)
            self.file_esclusi.write_text('\n'.join(sorted(self.esclusi)), encoding='utf-8')
            self._indicizza_persone()


STOP_SENZA_ACCENTI = frozenset(senza_accenti(w) for w in STOP)


def codici_del_testo(testo):
    """I codici del testo (pratiche, lotti, segnali: M462, EV-441), senza gli ID dei documenti, che ogni documento ha."""
    return [c for c in dict.fromkeys(RE_CODICE.findall(testo)) if not RE_ID_DOCUMENTO.match(c)]


def coppie_di(testo):
    """Le coppie di parole adiacenti nel testo, come sono scritte (minuscole, senza accenti, SENZA stemming): «supporto vuoto» sì; le coppie con una
    parola comune («il supporto») no. Senza stemming «supporto vuoto» e «supporti vuoti» sono frasi diverse, e la prima può stare in un brano solo."""
    t = [senza_accenti(w) for w in re.findall(r"[\wÀ-ÿ]+", testo.lower())]
    return frozenset((a, b) for a, b in zip(t, t[1:])
                     if len(a) > 1 and len(b) > 1 and a not in STOP_SENZA_ACCENTI and b not in STOP_SENZA_ACCENTI)


def frasi_di(brano):
    """Le coppie di parole adiacenti del brano (vedi `coppie_di`)."""
    return brano.get('frasi') or coppie_di(brano['testo'])


def tutto_nel_contesto(brani):
    return len(brani) <= SOGLIA_BRANI and sum(len(b['testo']) for b in brani) <= SOGLIA_CARATTERI


def _chiave_testo(testo):
    return re.sub(r'\W+', ' ', testo.lower()).strip()


def diversifica(candidati, punti, matrice, lam, quanti, gia=()):
    """I `quanti` brani da dare al modello tra i candidati (già in ordine di punteggio), con MMR: a ogni passo si prende il brano con
    lam * pertinenza - (1 - lam) * somiglianza con i brani già scelti. Con centinaia di documenti costruiti sullo stesso modello le
    varianti di uno stesso bollettino occuperebbero tutti i posti e il documento diverso (un collaudo, un esito) resterebbe fuori.
    `gia`: indici di brani già tra le fonti (non sono candidati, ma contano come «già visti» nella somiglianza).
    lam = 1, nessuna matrice di embedding o pochi candidati: i primi `quanti`, come prima."""
    if lam >= 1 or matrice is None or len(candidati) <= quanti:
        return candidati[:quanti]
    massimo = max(punti.get(i, 0.0) for i in candidati) or 1.0
    pertinenza = [punti.get(i, 0.0) / massimo for i in candidati]
    vettori = matrice[candidati]                                       # righe normalizzate: il prodotto è il coseno
    somiglianze = vettori @ vettori.T
    penalita = (vettori @ matrice[list(gia)].T).max(axis=1) if len(gia) else np.zeros(len(candidati), dtype=np.float32)
    rimasti, scelti = list(range(len(candidati))), []
    while rimasti and len(scelti) < quanti:
        k = max(rimasti, key=lambda k: lam * pertinenza[k] - (1 - lam) * float(penalita[k]))
        rimasti.remove(k)
        scelti.append(k)
        penalita = np.maximum(penalita, somiglianze[k])
    return sorted((candidati[k] for k in scelti), key=lambda i: punti.get(i, 0.0), reverse=True)     # l'ordine di lettura resta quello di pertinenza


def raggruppa_copie(candidati, matrice, soglia):
    """(candidati senza le copie, {brano tenuto: [indici delle sue copie]}). Due brani sono copie se il coseno dei loro embedding è almeno `soglia`
    (bollettini ripetuti con poche parole diverse). Si tiene il primo in ordine di punteggio; le copie non occupano un posto tra le fonti ma restano
    elencate, così il modello sa che il testo si ripete e può citarne l'ID. Senza embedding o con soglia ≥ 1 non cambia nulla."""
    if soglia >= 1 or matrice is None or len(candidati) < 2:
        return candidati, {}
    vettori = matrice[candidati]
    somiglianze = vettori @ vettori.T
    tenuti, copie = [], {}
    for k, i in enumerate(candidati):
        madre = next((t for t in tenuti if somiglianze[k, t] >= soglia), None)
        if madre is None:
            tenuti.append(k)
        else:
            copie.setdefault(candidati[madre], []).append(i)
    return [candidati[k] for k in tenuti], copie


def limita_per_documento(indici, brani, massimo=None):
    """Gli indici dei brani nello stesso ordine, ma al massimo `massimo` (MAX_BRANI_PER_DOC) per documento."""
    massimo = massimo or cfg.MAX_BRANI_PER_DOC
    contati, tenuti = Counter(), []
    for i in indici:
        if contati[brani[i]['doc']] < massimo:
            contati[brani[i]['doc']] += 1
            tenuti.append(i)
    return tenuti


def fonte_da_brano(b, punteggio=0, solo_titolo=None):
    """Un brano scelto, nel formato delle fonti passate al modello e mostrate nella pagina.
    solo_titolo: documento non pertinente di cui si passa solo il titolo, senza contenuto."""
    fonte = {'doc': b['doc'], 'id_interno': b['id_interno'], 'consultazione': b['consultazione'], 'sospetto': b.get('sospetto'),
             'giorno': b.get('giorno'), 'n': b['n'], 'testo': b['testo'], 'titolo': b.get('titolo'), 'punteggio': round(punteggio, 4)}
    if solo_titolo is not None:
        fonte.update(testo='', solo_titolo=solo_titolo or b['doc'])
    return fonte


def contesto_da_fonti(fonti):
    """[ID] testo, con un solo tag per documento (stesso formato di lazarus.py). Sono segnalati al modello i documenti
    di consultazione (voci enciclopediche, spesso messe per sviare): il modello può parlarne e citarli, ma non sono prove.
    Nessuna segnalazione dei documenti «sospetti»: se un testo dà ordini al modello lo scopre leggendolo (vedi SICUREZZA nelle istruzioni)."""
    per_doc = {}
    for f in fonti:
        per_doc.setdefault(f['doc'], []).append(f)
    # in ordine cronologico (giorno dell'indagine), così il modello legge i fatti nella sequenza in cui sono arrivati
    ordine = {d: k for k, d in enumerate(per_doc)}
    documenti = sorted(per_doc.items(), key=lambda kv: (kv[1][0].get('giorno') or 99, ordine[kv[0]]))

    def nota(f):
        giorno = f'(giorno {f["giorno"]}) ' if f.get('giorno') else ''
        if f.get('solo_titolo') is not None:
            return f'{giorno}{NOTA_CONSULTAZIONE} titolo: «{f["solo_titolo"]}». Contenuto non disponibile.'
        copie = f' (testo quasi identico anche in: {", ".join(f["copie"][:8])}{" …" if len(f["copie"]) > 8 else ""}) ' if f.get('copie') else ''
        # dal secondo brano in poi il testo non dice di che documento si tratta: il titolo lo ricorda al modello
        titolo = f'«{f["titolo"]}» … ' if f.get('n') and f.get('titolo') else ''
        return giorno + copie + (f'{NOTA_CONSULTAZIONE} ' if f.get('consultazione') else '') + titolo
    return '\n\n'.join(f'[{d}] ' + nota(v[0]) + ' '.join(x['testo'] for x in sorted(v, key=lambda x: x['n']))
                       for d, v in documenti)


NOTA_CONSULTAZIONE = '(documento NON PERTINENTE all\'indagine)'


def coseno(a, b):
    num = sum(x * y for x, y in zip(a, b))
    den = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return num / den if den else 0.0

