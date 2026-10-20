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
from testo import estrai_nomi, frasi, ms, parole

# --------------------------------------------------------------------- documenti
def leggi_file(percorso: Path):
    """Ritorna il testo di un file supportato (o None)."""
    est = percorso.suffix.lower()
    dati = percorso.read_bytes()
    return testo_da_bytes(percorso.name, dati) if est != '.zip' else None


def decodifica(dati):
    for codifica in ('utf-8-sig', 'cp1252', 'latin-1'):
        try:
            return dati.decode(codifica)
        except UnicodeDecodeError:
            pass
    return dati.decode('utf-8', errors='replace')


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


# "source: https://it.wikipedia.org/..." (txt) oppure "source": "https://..." (json): materiale di consultazione
RE_FONTE_ESTERNA = re.compile(r'''^\s*\{?\s*["']?source["']?\s*:\s*["']?https?://|["']source["']\s*:\s*["']https?://''', re.I | re.M)


# ID scritto nel documento: "ID G1-01", "Documento: LX4-1418", "id: ARCH-6408", {"id": "ARCH-6408", ...}
RE_ID_INTERNO = re.compile(r'''^\s*\{?\s*["']?(?:id|documento)["']?\s*:?\s*["']?([A-Z0-9][\w.-]{1,40})''', re.I | re.M)


def id_interno(testo):
    """L'ID con cui il documento si presenta e con cui il modello lo cita ([ARCH-6408] per documento_0421.json)."""
    m = RE_ID_INTERNO.search(testo[:600])
    return m[1] if m else None


def di_consultazione(testo):
    """Documenti presi da fonti esterne (es. voci di Wikipedia nell'archivio): si possono cercare,
    ma i loro nomi (città, aziende, personaggi storici) non sono persone del caso."""
    return bool(RE_FONTE_ESTERNA.search(testo[:5000]))


def spezza(doc_id, testo, max_car=900):
    """Divide un documento in brani di circa max_car caratteri, rispettando i paragrafi."""
    righe = [r for r in testo.strip().splitlines()]
    titolo = next((r.strip() for r in righe if r.strip() and not r.strip().upper().startswith('ID ')), doc_id)
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
        self.persone = {}        # nome -> [{doc, frase, contesto}]
        self.alias = {}          # alias -> nome
        self.embedding_attivo = embedding_attivo
        self.embedding_ok = False
        self.cache_emb = {}
        self.file_cache = CARTELLA_REGISTRO / 'cache_embedding.json'
        self.file_esclusi = CARTELLA_REGISTRO / 'non_persone.txt'
        self.esclusi = set()
        if self.file_cache.exists():
            try:
                self.cache_emb = json.loads(self.file_cache.read_text(encoding='utf-8'))
            except ValueError:
                self.cache_emb = {}
        if self.file_esclusi.exists():
            self.esclusi = {r.strip() for r in self.file_esclusi.read_text(encoding='utf-8').splitlines() if r.strip()}

    # -- caricamento
    def ricarica(self):
        docs = {}
        for cartella, origine in ((cfg.CARTELLA_DATI, 'squadra'), (CARTELLA_CARICATI, 'caricato')):
            if not cartella.exists():
                continue
            for p in sorted(cartella.rglob('*')):
                if p.is_file() and p.suffix.lower() in ESTENSIONI_OK - {'.zip'}:
                    try:
                        testo = leggi_file(p)
                    except Exception as e:  # file illeggibile: lo segnaliamo ma andiamo avanti
                        print(f'  ! {p.name}: {e}')
                        continue
                    doc_id = p.stem if p.stem not in docs else f'{p.stem}_{origine}'
                    docs[doc_id] = {'id': doc_id, 'nome': p.name, 'origine': origine,
                                    'percorso': str(p.relative_to(PROGETTO)) if p.is_relative_to(PROGETTO) else str(p),
                                    'testo': testo, 'consultazione': di_consultazione(testo), 'id_interno': id_interno(testo)}
        with self.lock:
            self.documenti = docs
            self._costruisci()

    def _costruisci(self):
        brani = []
        for d in self.documenti.values():
            titolo, pezzi = spezza(d['id'], d['testo'])
            d['titolo'] = titolo
            for n, t in enumerate(pezzi):
                p = parole(f'{titolo} {t}')
                brani.append({'doc': d['id'], 'id_interno': d.get('id_interno'), 'consultazione': d.get('consultazione', False),
                              'n': n, 'testo': t, 'parole': p, 'freq': Counter(p), 'emb': None})
        # BM25
        self.df = Counter()
        for b in brani:
            self.df.update(set(b['parole']))
        self.lung_media = (sum(len(b['parole']) for b in brani) / len(brani)) if brani else 1
        self.brani = brani
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
        for n in multipli:
            for t in n.split():
                if sum(t in m.split() for m in multipli) == 1 and t not in persone:
                    alias.setdefault(t, n)
        self.persone = dict(persone)
        self.alias = alias

    # -- embedding
    def calcola_embedding(self):
        if not self.embedding_attivo or tutto_nel_contesto(self.brani):
            return          # pochi testi: gli embedding non servono e farebbero caricare un secondo modello
        with self.lock:
            mancanti = [b for b in self.brani if self._chiave(b['testo']) not in self.cache_emb]
        try:
            for i in range(0, len(mancanti), 32):
                gruppo = mancanti[i:i + 32]
                vettori = embed([b['testo'] for b in gruppo])
                for b, v in zip(gruppo, vettori):
                    self.cache_emb[self._chiave(b['testo'])] = v
            with self.lock:
                for b in self.brani:
                    b['emb'] = self.cache_emb.get(self._chiave(b['testo']))
                self.embedding_ok = all(b['emb'] for b in self.brani) and bool(self.brani)
            CARTELLA_REGISTRO.mkdir(exist_ok=True)
            self.file_cache.write_text(json.dumps(self.cache_emb), encoding='utf-8')
        except Exception as e:
            self.embedding_ok = False
            print(f'  Embedding non disponibili ({e}). Uso solo la ricerca per parole chiave.')

    @staticmethod
    def _chiave(testo):
        return hashlib.sha1(f'{cfg.MODELLO_EMBEDDING}|{testo}'.encode()).hexdigest()

    # -- ricerca
    def cerca(self, domanda, strumenti, ipotesi=None):
        """Brani per la domanda: parole chiave (BM25) + significato (embedding) + HyDE se serve, fusi con RRF.
        `ipotesi`: funzione senza argomenti che ritorna la risposta ipotetica del modello (HyDE), o None."""
        with self.lock:
            brani = list(self.brani)
            n = len(brani)
        if not n:
            return []
        if tutto_nel_contesto(brani):
            # come lazarus.py: pochi testi -> si danno tutti al modello, niente ricerca né embedding
            strumenti.append({'nome': 'fondi_risultati', 'ms': 0, 'dettaglio': f'{n} brani: documenti passati interi'})
            return [{'doc': b['doc'], 'id_interno': b['id_interno'], 'n': b['n'], 'testo': b['testo'], 'punteggio': 0} for b in brani]
        t = time.perf_counter()
        q = parole(domanda)
        punteggi = []
        for i, b in enumerate(brani):
            freq = b['freq']
            s = 0.0
            for w in q:
                if w in freq:
                    idf = math.log(1 + (n - self.df[w] + 0.5) / (self.df[w] + 0.5))
                    f = freq[w]
                    s += idf * f * 2.2 / (f + 1.2 * (0.25 + 0.75 * len(b['parole']) / self.lung_media))
            punteggi.append((s, i))
        bm25 = [i for s, i in sorted(punteggi, reverse=True) if s > 0][:30]
        strumenti.append({'nome': 'cerca_parole_chiave', 'ms': ms(t), 'dettaglio': f'{len(bm25)} brani con parole in comune'})

        semantica, migliore = [], None
        if self.embedding_ok:
            t = time.perf_counter()
            try:
                qv = embed([domanda])[0]
                sim = sorted(((coseno(qv, b['emb']), i) for i, b in enumerate(brani)), reverse=True)
                semantica, migliore = [i for s, i in sim[:30]], sim[0][0]
                strumenti.append({'nome': 'cerca_semantica', 'ms': ms(t), 'dettaglio': f'migliore similarità {migliore:.2f}'})
            except Exception as e:
                strumenti.append({'nome': 'cerca_semantica', 'ms': ms(t), 'dettaglio': f'errore: {e}'})

        # HyDE: solo se la domanda da sola trova poco. È una classifica IN PIÙ: un'ipotesi sbagliata
        # non può togliere i brani trovati con la domanda vera.
        hyde = []
        if ipotesi and migliore is not None:
            t = time.perf_counter()
            if migliore >= cfg.HYDE_SOGLIA:
                strumenti.append({'nome': 'cerca_hyde', 'ms': 0, 'dettaglio': f'non serve: similarità {migliore:.2f} ≥ {cfg.HYDE_SOGLIA:.2f}'})
            else:
                try:
                    testo_ipotesi = ipotesi()
                    hv = embed([testo_ipotesi])[0]
                    sim_h = sorted(((coseno(hv, b['emb']), i) for i, b in enumerate(brani)), reverse=True)
                    hyde = [i for s, i in sim_h[:30]]
                    strumenti.append({'nome': 'cerca_hyde', 'ms': ms(t), 'testo': testo_ipotesi,
                                      'dettaglio': f'risposta ipotetica di {len(testo_ipotesi.split())} parole · migliore similarità {sim_h[0][0]:.2f}'})
                except Exception as e:
                    strumenti.append({'nome': 'cerca_hyde', 'ms': ms(t), 'dettaglio': f'errore: {e}'})

        t = time.perf_counter()
        rrf = defaultdict(float)
        for classifica in (bm25, semantica, hyde):
            for pos, i in enumerate(classifica):
                rrf[i] += 1 / (60 + pos)
        for i in rrf:   # materiale di consultazione (voci di Wikipedia): cercabile, ma il caso viene prima
            if brani[i]['consultazione']:
                rrf[i] *= cfg.PESO_CONSULTAZIONE
        scelti = sorted(rrf, key=rrf.get, reverse=True)[:TOP_K]
        if not scelti:  # nessuna parola in comune e niente embedding: prendiamo i primi brani
            scelti = list(range(min(TOP_K, n)))
        di_consultazione = sum(brani[i]['consultazione'] for i in scelti)
        strumenti.append({'nome': 'fondi_risultati', 'ms': ms(t), 'dettaglio': f'{len(scelti)} brani scelti'
                          + (f' ({di_consultazione} di consultazione)' if di_consultazione else '')})
        return [{'doc': brani[i]['doc'], 'id_interno': brani[i]['id_interno'], 'n': brani[i]['n'], 'testo': brani[i]['testo'],
                 'punteggio': round(rrf.get(i, 0), 4)} for i in scelti]

    def persone_in(self, testo):
        """Persone citate in un testo, solo se compaiono anche nei documenti (nomi inventati o etichette no)."""
        trovate = []
        with self.lock:
            noti = list(self.persone) + list(self.alias)
            for nome in sorted(noti, key=len, reverse=True):
                if re.search(rf'(?<![\w]){re.escape(nome)}(?![\w])', testo):
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


def tutto_nel_contesto(brani):
    return len(brani) <= SOGLIA_BRANI and sum(len(b['testo']) for b in brani) <= SOGLIA_CARATTERI


def contesto_da_fonti(fonti):
    """[ID] testo, con un solo tag per documento (stesso formato di lazarus.py)."""
    per_doc = {}
    for f in fonti:
        per_doc.setdefault(f['doc'], []).append(f)
    return '\n\n'.join(f'[{d}] ' + ' '.join(x['testo'] for x in sorted(v, key=lambda x: x['n'])) for d, v in per_doc.items())


def coseno(a, b):
    num = sum(x * y for x, y in zip(a, b))
    den = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return num / den if den else 0.0

