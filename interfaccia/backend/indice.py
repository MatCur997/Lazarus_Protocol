"""Documenti, brani, ricerca (BM25 + embedding) e indice delle persone. Tutto in memoria."""
import hashlib
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
from config import (CARTELLA_CARICATI, CARTELLA_REGISTRO, ESTENSIONI_OK, ESTENSIONI_TESTO, PROGETTO,
                    SOGLIA_BRANI, SOGLIA_CARATTERI, TOP_K)
from modello import embed
from testo import estrai_nomi, frasi, ms, parole

# --------------------------------------------------------------------- documenti
def leggi_file(percorso: Path):
    """Ritorna il testo di un file supportato (o None)."""
    est = percorso.suffix.lower()
    dati = percorso.read_bytes()
    return testo_da_bytes(percorso.name, dati) if est != '.zip' else None


def testo_da_bytes(nome, dati):
    est = Path(nome).suffix.lower()
    if est in ESTENSIONI_TESTO:
        for codifica in ('utf-8-sig', 'cp1252', 'latin-1'):
            try:
                return dati.decode(codifica)
            except UnicodeDecodeError:
                pass
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
                                    'testo': testo}
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
                brani.append({'doc': d['id'], 'n': n, 'testo': t, 'parole': p, 'freq': Counter(p), 'emb': None})
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
    def cerca(self, domanda, strumenti):
        with self.lock:
            brani = list(self.brani)
            n = len(brani)
        if not n:
            return []
        if tutto_nel_contesto(brani):
            # come lazarus.py: pochi testi -> si danno tutti al modello, niente ricerca né embedding
            strumenti.append({'nome': 'fondi_risultati', 'ms': 0, 'dettaglio': f'{n} brani: documenti passati interi'})
            return [{'doc': b['doc'], 'n': b['n'], 'testo': b['testo'], 'punteggio': 0} for b in brani]
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

        semantica = []
        if self.embedding_ok:
            t = time.perf_counter()
            try:
                qv = embed([domanda])[0]
                sim = sorted(((coseno(qv, b['emb']), i) for i, b in enumerate(brani)), reverse=True)
                semantica = [i for s, i in sim[:30]]
                strumenti.append({'nome': 'cerca_semantica', 'ms': ms(t), 'dettaglio': f'migliore similarità {sim[0][0]:.2f}'})
            except Exception as e:
                strumenti.append({'nome': 'cerca_semantica', 'ms': ms(t), 'dettaglio': f'errore: {e}'})

        t = time.perf_counter()
        rrf = defaultdict(float)
        for classifica in (bm25, semantica):
            for pos, i in enumerate(classifica):
                rrf[i] += 1 / (60 + pos)
        scelti = sorted(rrf, key=rrf.get, reverse=True)[:TOP_K]
        if not scelti:  # nessuna parola in comune e niente embedding: prendiamo i primi brani
            scelti = list(range(min(TOP_K, n)))
        strumenti.append({'nome': 'fondi_risultati', 'ms': ms(t), 'dettaglio': f'{len(scelti)} brani scelti'})
        return [{'doc': brani[i]['doc'], 'n': brani[i]['n'], 'testo': brani[i]['testo'],
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

