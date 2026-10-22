"""Investigatore LAZZARO - server locale (solo HTTP: instrada le richieste e serve la pagina).

Usa solo la libreria standard di Python. Ascolta soltanto su 127.0.0.1 e parla
solo con Ollama in locale: nessun dato lascia il computer.

Dove si cambia cosa:
  config.py   impostazioni, prompt, collegamento con app_lazarus
  motore.py   i passi di una risposta: controlla_pertinenza -> cerca_fonti -> costruisci_messaggi -> genera
  indice.py   documenti, brani, ricerca BM25 + embedding
  modello.py  chiamate a Ollama
  testo.py    funzioni sul testo (nomi, luoghi, indizi)
  storico.py  storico delle domande
  volti.py    foto fissa per ogni persona (sesso dedotto dal nome)
  server.py   questo file: rotte HTTP

Avvio:   python interfaccia/backend/server.py            (con Ollama)
         python interfaccia/backend/server.py --demo     (senza modello, per provare l'interfaccia)
Le pagine servite sono in interfaccia/frontend/. Mappa completa: ARCHITETTURA.md nella cartella del progetto.
"""
import argparse
import io
import json
import re
import socket
import sys
import threading
import time
import urllib.error
import uuid
import webbrowser
import zipfile
from collections import Counter, defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import config as cfg
import motore
from config import (CARTELLA_CARICATI, CARTELLA_REGISTRO, ESTENSIONI_OK, ESTRAZIONE_CON_MODELLO, MAX_UPLOAD, PORTA, PROGETTO, STATICI,
                    STRUMENTI, TOP_K)
from indice import Indice
from modello import estrai_con_modello, modelli_disponibili, precarica, verifica_citazione
from storico import Storico
from volti import Volti
from testo import chiave, chiave_id, estrai_luoghi, estrai_voci_nominate, frasi, ms, parole
from indice import testo_da_bytes

# ------------------------------------------------------------------------- server
STATO = {'modello': cfg.MODELLO, 'demo': False}
INDICE: Indice = None
STORICO: Storico = None
VOLTI: Volti = None
EVENTI_CARICAMENTO = []   # chiamate a carica_file, per il pannello strumenti
TURNO = {'n': 0}          # numero dell'ultima domanda: serve a interrompere estrazioni ormai superate
FILI_PER_VOCE = 2         # bacheca: collegamenti più forti tenuti per ogni indizio
FILO_MINIMO = 1.0         # bacheca: punteggio minimo perché due indizi siano collegati
BLOCCO_TURNO = threading.Lock()

def del_caso(fonte):
    """Una citazione è una prova solo se è verificata e viene da un documento del caso: le voci di consultazione
    (enciclopedia, spesso messe per sviare) servono a rispondere ma non diventano indizi, eventi o luoghi."""
    d = INDICE.documenti.get(fonte.get('doc') or '')
    return bool(fonte.get('verificato')) and not (d and d.get('consultazione'))


TIPI = {'.html': 'text/html; charset=utf-8', '.css': 'text/css; charset=utf-8',
        '.js': 'application/javascript; charset=utf-8', '.svg': 'image/svg+xml', '.ico': 'image/x-icon'}


class Gestore(BaseHTTPRequestHandler):
    server_version = 'Lazzaro/1.0'

    def log_message(self, fmt, *args):  # niente rumore nel terminale
        pass

    # -- risposte
    def json(self, dati, codice=200):
        corpo = json.dumps(dati, ensure_ascii=False).encode()
        self.send_response(codice)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(corpo)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(corpo)

    def corpo_json(self):
        n = int(self.headers.get('Content-Length') or 0)
        return json.loads(self.rfile.read(n) or b'{}')

    # -- GET
    def do_GET(self):
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        rotte = {
            '/api/stato': self.api_stato,
            '/api/storico': lambda: self.json(STORICO.voci),
            '/api/strumenti': self.api_strumenti,
            '/api/documenti': self.api_documenti,
            '/api/persone': self.api_persone,
            '/api/persona': lambda: self.api_persona(q.get('nome', '')),
            '/api/documento': lambda: self.api_documento(q.get('id', '')),
            '/api/cerca': lambda: self.api_cerca(q.get('q', '')),
            '/api/elementi': self.api_elementi,
            '/api/bacheca': self.api_bacheca,
            '/api/elemento': lambda: self.api_elemento(q.get('tipo', ''), q.get('chiave', '')),
            '/api/ragview/archivio': self.api_ragview_archivio,
            '/api/ragview/domanda': lambda: self.api_ragview_domanda(q.get('q', '')),
        }
        if url.path in rotte:
            try:
                return rotte[url.path]()
            except ValueError as e:
                return self.json({'errore': str(e)}, 400)
        nome = 'index.html' if url.path in ('/', '') else unquote(url.path.lstrip('/'))
        percorso = (STATICI / nome).resolve()
        if not percorso.is_relative_to(STATICI) or not percorso.is_file():
            return self.json({'errore': 'non trovato'}, 404)
        dati = percorso.read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', TIPI.get(percorso.suffix, 'application/octet-stream'))
        self.send_header('Content-Length', str(len(dati)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(dati)

    def api_stato(self):
        modelli, ok = ([], False) if STATO['demo'] else modelli_disponibili()
        generativi = [m for m in modelli if not m.startswith(cfg.MODELLO_EMBEDDING.split(':')[0]) and 'embed' not in m]
        self.json({'modello': STATO['modello'], 'modelli': generativi, 'ollama': ok, 'demo': STATO['demo'],
                   'embedding': INDICE.embedding_ok, 'modello_embedding': cfg.MODELLO_EMBEDDING,
                   'documenti': len(INDICE.documenti), 'brani': len(INDICE.brani), 'top_k': TOP_K,
                   'avviso': cfg.AVVISO_LAZARUS, 'pertinenza': INDICE.info_pertinenza})

    def api_strumenti(self):
        uso = defaultdict(list)
        for v in STORICO.voci:
            for s in v.get('strumenti', []):
                uso[s['nome']].append(s)
        for s in EVENTI_CARICAMENTO:
            uso['carica_file'].append(s)
        elenco = []
        for nome, desc in STRUMENTI.items():
            chiamate = uso.get(nome, [])
            elenco.append({'nome': nome, 'descrizione': desc, 'chiamate': len(chiamate),
                           'ms_medio': round(sum(c['ms'] for c in chiamate) / len(chiamate), 1) if chiamate else None,
                           'ultimo': chiamate[-1] if chiamate else None,
                           'attivo': nome != 'cerca_semantica' or INDICE.embedding_usabile})
        self.json(elenco)

    def api_documenti(self):
        with INDICE.lock:
            self.json([{'id': d['id'], 'nome': d['nome'], 'titolo': d.get('titolo', ''), 'origine': d['origine'],
                        'id_interno': d.get('id_interno'), 'consultazione': d.get('consultazione', False),
                        'pertinenza': d.get('pertinenza'), 'giorno': d.get('giorno'),
                        'sospetto': d.get('sospetto'), 'sospetto_manuale': d.get('sospetto_manuale'),
                        'caratteri': len(d['testo'])} for d in INDICE.documenti.values()])

    def api_documento(self, doc_id):
        d = INDICE.documenti.get(doc_id)
        if not d:
            return self.json({'errore': 'documento non trovato'}, 404)
        self.json({k: d[k] for k in ('id', 'nome', 'titolo', 'origine', 'testo')})

    @staticmethod
    def passaggi(corrisponde, solo_doc=None, solo_caso=False):
        """Frasi dei documenti per cui corrisponde(chiave_frase) è vero, con la frase prima e dopo come contesto.
        solo_caso: salta i documenti di consultazione (non sono prove)."""
        documenti = []
        with INDICE.lock:
            for d in INDICE.documenti.values():
                if (solo_doc and d['id'] not in solo_doc) or (solo_caso and d.get('consultazione')):
                    continue
                elenco = frasi(d['testo'])
                brani = [{'frase': f, 'contesto': ' '.join(elenco[max(0, i - 1):i + 2])}
                         for i, f in enumerate(elenco) if corrisponde(chiave(f))]
                if brani:
                    documenti.append({'id': d['id'], 'nome': d['nome'], 'titolo': d.get('titolo', ''), 'brani': brani})
        return documenti

    def api_cerca(self, termine):
        """Ricerca testuale semplice (senza accenti, senza maiuscole) per la barra in alto."""
        cercato = chiave(termine)
        documenti = self.passaggi(lambda k: cercato in k) if cercato else []
        self.json({'termine': termine, 'documenti': documenti})

    def api_elementi(self):
        """Tutto ciò che serve alla colonna destra: persone, luoghi, indizi, eventi."""
        elenchi = {'persone': self.elenco_persone()}
        for tipo in ('luoghi', 'indizi', 'eventi'):
            elenco = []
            for r in STORICO.raccogli(tipo).values():
                voce = {k: r[k] for k in ('chiave', 'nome', 'citazioni', 'ultima')}
                prove = [f for f in r['fonti'] if del_caso(f)]
                voce['verificato'] = bool(prove)
                voce['doc'] = prove[0]['doc'] if prove else None
                voce['estratto'] = prove[0]['citazione'] if prove else ''
                if tipo == 'luoghi':
                    voce['documenti'] = len(self.passaggi(lambda k, c=r['chiave']: c in k, solo_caso=True))
                    voce['verificato'] = voce['documenti'] > 0
                if not voce['verificato']:      # senza riscontro nei documenti non entra nella colonna
                    continue
                elenco.append(voce)
            elenco.sort(key=lambda x: (-x['citazioni'], x['ultima']), reverse=False)
            elenchi[tipo] = elenco
        self.json(elenchi)

    # ---------------------------------------------------------------- RAG view
    def api_ragview_archivio(self):
        """Archivio visto dal motore: documenti con cluster, pertinenza e coordinate 2D; riepilogo dei cluster."""
        with INDICE.lock:
            mappa = INDICE.mappa_pertinenza or {}
            etichette, coordinate = mappa.get('etichette', {}), mappa.get('coordinate') or {}
            documenti = [{'id': d['id'], 'titolo': d.get('titolo', ''), 'pertinente': not d.get('consultazione'),
                          'sospetto': bool(d.get('sospetto')), 'somiglianza': d.get('pertinenza'), 'giorno': d.get('giorno'),
                          'cluster': etichette.get(d['id']), 'xyz': coordinate.get(d['id'])} for d in INDICE.documenti.values()]
            n_brani = len(INDICE.brani)
        cluster = []
        for c in mappa.get('cluster', []):
            membri = [d for d in documenti if d['cluster'] == c['id']]
            cluster.append({**c, 'esempi': [d['titolo'] for d in membri[:6]]})
        cluster.sort(key=lambda c: -c['vicinanza'])
        self.json({'info': INDICE.info_pertinenza, 'K': mappa.get('K'), 'cluster': cluster, 'documenti': documenti,
                   'brani': n_brani, 'embedding': INDICE.embedding_ok, 'modello_embedding': cfg.MODELLO_EMBEDDING,
                   'top_k': TOP_K, 'hyde': cfg.HYDE, 'hyde_soglia': cfg.HYDE_SOGLIA})

    def api_ragview_domanda(self, domanda):
        """Il percorso di una domanda fino al prompt, SENZA generare la risposta e senza scrivere nello storico."""
        domanda = domanda.strip()
        if not domanda:
            raise ValueError('Domanda vuota')
        t0 = time.perf_counter()
        strumenti, traccia = [], {}
        modello = STATO['modello']
        if motore.chiede_dei_documenti(domanda):
            conversazione, motivo = False, 'domanda sui documenti (sospetti o estranei) · regole'
        else:
            conversazione = motore.controlla_pertinenza(INDICE, modello, domanda, strumenti, STATO['demo'])
            motivo = strumenti[-1]['dettaglio'] if strumenti else ''
        fonti = [] if conversazione else motore.cerca_fonti(INDICE, domanda, [], strumenti, modello, STATO['demo'], traccia)
        messaggi = motore.costruisci_messaggi(domanda, fonti, [], conversazione,
                                              nota_archivio='' if conversazione else motore.note_per_prompt(INDICE, domanda))
        caratteri = sum(len(m['content']) for m in messaggi)
        self.json({'domanda': domanda, 'conversazione': conversazione, 'motivo': motivo, 'strumenti': strumenti,
                   'traccia': traccia, 'ms': ms(t0),
                   'fonti': [{'doc': f['doc'], 'n': f['n'], 'solo_titolo': f.get('solo_titolo'), 'sospetto': bool(f.get('sospetto')),
                              'pertinente': not f.get('consultazione'), 'giorno': f.get('giorno'), 'caratteri': len(f['testo'])} for f in fonti],
                   'messaggi': messaggi, 'token_stimati': caratteri // 3})

    def riepilogo_verificato(self, massimo=14):
        """Indizi, ipotesi, eventi e persone confermati dai documenti del caso, per i riassunti in chat."""
        righe = []
        for tipo in ('eventi', 'indizi'):
            for r in sorted(STORICO.raccogli(tipo).values(), key=lambda r: -r['citazioni']):
                prove = [f for f in r['fonti'] if del_caso(f)]
                if prove:
                    genere = r.get('genere') or ('evento' if tipo == 'eventi' else 'indizio')
                    nome = re.sub(r'\s*\((indizio|ipotesi)\)$', '', r['nome'])
                    righe.append(f"- {genere.capitalize()}: {nome} — {r.get('descrizione') or prove[0]['citazione']} [{prove[0]['doc']}]")
        persone = [p['nome'] for p in self.elenco_persone()]
        return '\n'.join(righe[:massimo] + ([f"- Persone: {', '.join(persone)}"] if persone else []))

    def api_bacheca(self):
        """Bacheca dell'investigatore. Nodi: indizi, ipotesi ed eventi verificati, e le persone trovate (con volto).
        Fili: ogni persona è collegata alle voci che la riguardano; tra indizi/ipotesi/eventi ogni coppia riceve
        un punteggio (contenuto simile, stessa risposta, stessa persona, stesso documento) e ogni voce tiene solo
        i suoi FILI_PER_VOCE collegamenti più forti, così la bacheca resta leggibile.
        Si ricalcola a ogni richiesta: le nuove voci compaiono appena trovate."""
        domande = {v['id']: v['domanda'] for v in STORICO.voci}
        nodi, testi = [], {}            # testi: id del nodo -> testo (chiave) in cui cercare i luoghi
        for tipo in ('indizi', 'eventi'):
            for r in STORICO.raccogli(tipo).values():
                fonti = [f for f in r['fonti'] if del_caso(f)]
                if not fonti:
                    continue
                genere = r.get('genere') or ('evento' if tipo == 'eventi' else 'ipotesi' if r['nome'].endswith('(ipotesi)') else 'indizio')
                testo = ' '.join([r['nome'], r.get('descrizione', '')] + [f['citazione'] for f in fonti])
                nodi.append({'id': f"{tipo}:{r['chiave']}", 'tipo': tipo, 'chiave': r['chiave'],
                             'nome': r['nome'].removesuffix(f' ({genere})'), 'genere': genere,
                             'documenti': sorted({f['doc'] for f in fonti if f['doc']}), 'citazione': fonti[0]['citazione'],
                             'descrizione': r.get('descrizione', ''), 'citazioni': r['citazioni'], 'ultima': r['ultima'],
                             'risposte': r['risposte'], 'domande': [domande[i] for i in r['risposte'] if i in domande],
                             'persone': INDICE.persone_in(testo)})
                testi[nodi[-1]['id']] = chiave(testo)
        radici ={n['id']: {p[:5] for p in parole(f"{n['nome']} {n['descrizione']}")} for n in nodi}
        candidati = []
        for i, a in enumerate(nodi):
            for b in nodi[i + 1:]:
                ra, rb = radici[a['id']], radici[b['id']]
                simile = len(ra & rb) / max(1, len(ra | rb))
                stessi_doc = sorted(set(a['documenti']) & set(b['documenti']))
                stessa_risposta = bool(set(a['risposte']) & set(b['risposte']))
                stesse_persone = sorted(set(a['persone']) & set(b['persone']))
                punteggio = 2 * simile + 0.5 * len(stessi_doc) + 1 * stessa_risposta + 0.5 * len(stesse_persone)
                motivi = (['contenuto simile'] if simile >= 0.2 else []) + (['nati dalla stessa risposta'] if stessa_risposta else []) \
                    + [f'riguardano {p}' for p in stesse_persone] + [f'stesso documento {d}' for d in stessi_doc]
                if punteggio >= FILO_MINIMO and motivi:
                    candidati.append((punteggio, a['id'], b['id'], motivi))
        # ogni voce tiene i suoi collegamenti più forti; un filo resta se è tra i migliori di almeno una delle due
        tenuti, per_voce = set(), defaultdict(int)
        for punteggio, da, a, motivi in sorted(candidati, reverse=True):
            if per_voce[da] < FILI_PER_VOCE or per_voce[a] < FILI_PER_VOCE:
                tenuti.add((da, a))
                per_voce[da] += 1
                per_voce[a] += 1
        fili = [{'da': da, 'a': a, 'motivi': motivi, 'forza': round(p, 2)} for p, da, a, motivi in candidati if (da, a) in tenuti]

        # persone: una scheda con volto e i dati che abbiamo, e un filo verso ogni voce che le riguarda
        for p in self.elenco_persone():
            with INDICE.lock:
                occorrenze = INDICE.persone.get(p['nome'], [])
                alias = [a for a, n in INDICE.alias.items() if n == p['nome']]
            frasi_viste, frasi_doc = set(), []
            for o in occorrenze:
                if o['frase'] not in frasi_viste and len(frasi_doc) < 2:
                    frasi_viste.add(o['frase'])
                    frasi_doc.append({'doc': o['doc'], 'frase': o['frase']})
            id_p = f"persone:{p['nome']}"
            collegate = [n for n in nodi if p['nome'] in n['persone']]
            fili += [{'da': id_p, 'a': n['id'], 'motivi': [f"riguarda {p['nome']}"], 'forza': 2} for n in collegate]
            documenti = sorted({o['doc'] for o in occorrenze})
            nodi.append({'id': id_p, 'tipo': 'persone', 'chiave': p['nome'], 'nome': p['nome'], 'genere': 'persona',
                         'documenti': documenti, 'frasi': frasi_doc, 'alias': alias, 'citazioni': p['citazioni'],
                         'ultima': p['ultima'], 'volto': p.get('volto'), 'persone': [], 'domande': [],
                         'descrizione': f"Compare in {len(documenti)} {'documento' if len(documenti) == 1 else 'documenti'}"
                                        f" ({', '.join(documenti)}), è citata in {p['citazioni']} "
                                        f"{'risposta' if p['citazioni'] == 1 else 'risposte'} ed è collegata a "
                                        f"{len(collegate)} {'voce' if len(collegate) == 1 else 'voci'} della bacheca."})
            testi[id_p] = chiave(' '.join(o['contesto'] for o in occorrenze))

        # luoghi: solo se compaiono davvero nei documenti (come nella colonna destra); un filo verso ogni
        # indizio, ipotesi, evento o persona il cui testo li nomina
        for r in STORICO.raccogli('luoghi').values():
            documenti = self.passaggi(lambda k, c=r['chiave']: c in k, solo_caso=True)
            if not documenti:
                continue
            id_l = f"luoghi:{r['chiave']}"
            collegate = [n for n in nodi if r['chiave'] in testi.get(n['id'], '')]
            fili += [{'da': id_l, 'a': n['id'], 'motivi': [f"parla di {r['nome']}"], 'forza': 1.5} for n in collegate]
            frasi_doc = [{'doc': d['id'], 'frase': b['frase']} for d in documenti for b in d['brani'][:1]][:2]
            ids = [d['id'] for d in documenti]
            elenco_doc = ', '.join(ids[:6]) + (f' e altri {len(ids) - 6}' if len(ids) > 6 else '')
            nodi.append({'id': id_l, 'tipo': 'luoghi', 'chiave': r['chiave'], 'nome': r['nome'], 'genere': 'luogo',
                         'documenti': ids, 'frasi': frasi_doc, 'citazioni': r['citazioni'], 'ultima': r['ultima'],
                         'persone': [], 'domande': [domande[i] for i in r['risposte'] if i in domande],
                         'descrizione': f"Compare in {len(ids)} {'documento' if len(ids) == 1 else 'documenti'} ({elenco_doc}) "
                                        f"ed è collegato a {len(collegate)} {'voce' if len(collegate) == 1 else 'voci'} della bacheca."})
        for n in nodi:
            n.pop('risposte', None)
        self.json({'nodi': nodi, 'fili': fili, 'aggiornata': time.strftime('%H:%M:%S')})

    def api_elemento(self, tipo, k):
        if tipo not in ('luoghi', 'indizi', 'eventi'):
            raise ValueError('Tipo sconosciuto')
        r = STORICO.raccogli(tipo).get(k)
        if not r:
            return self.json({'errore': 'elemento non trovato'}, 404)
        if tipo == 'luoghi':
            documenti = self.passaggi(lambda x: k in x)
        else:   # i passaggi sono quelli citati dal modello (o dalle regole), verificati nel testo
            citate = [chiave(f['citazione']) for f in r['fonti']]
            documenti = self.passaggi(lambda x: any(x and (x in c or c in x) for c in citate),
                                      solo_doc={f['doc'] for f in r['fonti'] if f['doc']} or None)
        risposte = [{'id': v['id'], 'ora': v['ora'], 'domanda': v['domanda'], 'risposta': v['risposta'], 'modello': v['modello']}
                    for v in STORICO.voci if v['id'] in r['risposte']]
        self.json({'tipo': tipo, 'chiave': k, 'nome': r['nome'], 'fonti': r['fonti'], 'documenti': documenti, 'risposte': risposte})

    def elenco_persone(self):
        conteggio, ultima = Counter(), {}
        for v in STORICO.voci:
            for p in v.get('persone', []):
                conteggio[p] += 1
                ultima[p] = v['ora']
        with INDICE.lock:
            elenco = [{'nome': p, 'citazioni': c, 'ultima': ultima[p],
                       'documenti': len({o['doc'] for o in INDICE.persone.get(p, [])}),
                       'nei_documenti': p in INDICE.persone}
                      for p, c in conteggio.items() if p not in INDICE.esclusi and p in INDICE.persone]   # solo con riscontro
        for p in elenco:
            p['volto'] = VOLTI.volto_di(p['nome'])      # foto fissa, assegnata alla prima comparsa
        elenco.sort(key=lambda x: (-x['citazioni'], x['nome']))
        return elenco

    def api_persone(self):
        self.json(self.elenco_persone())

    def api_persona(self, nome):
        with INDICE.lock:
            nome = INDICE.alias.get(nome, nome)
            occorrenze = INDICE.persone.get(nome, [])
            alias = [a for a, n in INDICE.alias.items() if n == nome]
            per_doc = defaultdict(list)
            for o in occorrenze:
                per_doc[o['doc']].append(o)
            documenti = [{'id': d, 'nome': INDICE.documenti[d]['nome'], 'titolo': INDICE.documenti[d].get('titolo', ''),
                          'brani': [{'frase': o['frase'], 'contesto': o['contesto']} for o in oo]}
                         for d, oo in per_doc.items() if d in INDICE.documenti]
        risposte = [{'id': v['id'], 'ora': v['ora'], 'domanda': v['domanda'], 'risposta': v['risposta'], 'modello': v['modello']}
                    for v in STORICO.voci if nome in v.get('persone', [])]
        volto = VOLTI.volto_di(nome) if occorrenze else None   # solo per le persone che compaiono nei documenti
        self.json({'nome': nome, 'alias': alias, 'documenti': documenti, 'risposte': risposte, 'volto': volto})

    # -- POST
    def do_POST(self):
        url = urlparse(self.path)
        try:
            if url.path == '/api/chiedi':
                return self.api_chiedi(self.corpo_json())
            if url.path == '/api/modello':
                return self.api_modello(self.corpo_json())
            if url.path == '/api/carica':
                return self.api_carica()
            if url.path == '/api/reindicizza':
                return self.api_reindicizza()
            if url.path == '/api/caricati/svuota':
                return self.api_svuota_caricati()
            if url.path == '/api/documento/sospetto':      # {id, sospetto: true/false/null} null = automatico
                corpo = self.corpo_json()
                return self.json({'sospetto': INDICE.imposta_sospetto(corpo.get('id', ''), corpo.get('sospetto'))})
            if url.path == '/api/rimuovi':
                return self.api_rimuovi(self.corpo_json())
            if url.path == '/api/storico/elimina':
                return self.json({'eliminate': STORICO.elimina(self.corpo_json().get('id') or '__nessuna__')})
            if url.path == '/api/storico/svuota':
                return self.json({'eliminate': STORICO.elimina(None)})
            if url.path == '/api/persona/escludi':
                INDICE.escludi(self.corpo_json().get('nome', ''))
                return self.json({'ok': True})
            if url.path == '/api/elemento/escludi':
                corpo = self.corpo_json()
                STORICO.escludi(corpo.get('tipo', ''), corpo.get('chiave', ''))
                return self.json({'ok': True})
            self.json({'errore': 'non trovato'}, 404)
        except (ValueError, KeyError, zipfile.BadZipFile) as e:
            self.json({'errore': str(e)}, 400)

    def api_modello(self, corpo):
        STATO['modello'] = corpo.get('modello') or STATO['modello']
        if not STATO['demo']:
            threading.Thread(target=precarica, args=(STATO['modello'],), daemon=True).start()
        self.json({'modello': STATO['modello']})

    def api_carica(self):
        n = int(self.headers.get('Content-Length') or 0)
        if n > MAX_UPLOAD:
            raise ValueError('File troppo grande (massimo 50 MB)')
        nome = Path(unquote(self.headers.get('X-Nome-File', 'file.txt'))).name
        # file di una cartella: "Cartella/sotto/file.txt" -> si salva in caricati/Cartella/sotto/
        relativo = unquote(self.headers.get('X-Percorso', '')).replace('\\', '/')
        parti = [p for p in relativo.split('/')[:-1] if p not in ('', '.')]
        if any(p == '..' or ':' in p for p in parti):
            raise ValueError('Percorso non valido')
        cartella = CARTELLA_CARICATI.joinpath(*parti)
        if not cartella.resolve().is_relative_to(CARTELLA_CARICATI.resolve()):
            raise ValueError('Percorso non valido')
        dati = self.rfile.read(n)
        t = time.perf_counter()
        est = Path(nome).suffix.lower()
        if est not in ESTENSIONI_OK:
            raise ValueError(f'Formato non supportato: {est or "senza estensione"}. Usa txt, md, csv, json, xml, html, docx, pdf, xlsx o zip.')
        cartella.mkdir(parents=True, exist_ok=True)
        salvati = []
        if est == '.zip':
            destinazione = cartella / Path(nome).stem
            with zipfile.ZipFile(io.BytesIO(dati)) as z:
                for item in z.infolist():
                    bersaglio = (destinazione / item.filename).resolve()
                    if not bersaglio.is_relative_to(destinazione.resolve()):
                        raise ValueError('Percorso non valido nello ZIP')
                    if item.is_dir() or Path(item.filename).suffix.lower() not in ESTENSIONI_OK - {'.zip'}:
                        continue
                    bersaglio.parent.mkdir(parents=True, exist_ok=True)
                    bersaglio.write_bytes(z.read(item))
                    salvati.append(str(bersaglio.relative_to(CARTELLA_CARICATI)))
        else:
            testo_da_bytes(nome, dati)          # verifica che sia leggibile prima di salvarlo
            (cartella / nome).write_bytes(dati)
            salvati.append(str((cartella / nome).relative_to(CARTELLA_CARICATI)))
        if self.headers.get('X-Senza-Indice') == '1':   # cartella: l'indice si ricostruisce una volta sola alla fine
            return self.json({'salvati': salvati, 'ms': ms(t)})
        INDICE.ricarica()
        INDICE.calcola_embedding()
        evento = {'nome': 'carica_file', 'ms': ms(t), 'dettaglio': f'{nome}: {len(salvati)} file'}
        EVENTI_CARICAMENTO.append(evento)
        self.json({'salvati': salvati, 'documenti': len(INDICE.documenti), 'brani': len(INDICE.brani), 'ms': evento['ms']})

    def api_reindicizza(self):
        """Dopo il caricamento di una cartella: un solo ricalcolo dell'indice per tutti i file."""
        corpo = self.corpo_json()
        t = time.perf_counter()
        INDICE.ricarica()
        INDICE.calcola_embedding()
        evento = {'nome': 'carica_file', 'ms': ms(t), 'dettaglio': f"{corpo.get('nome', 'cartella')}: {corpo.get('file', 0)} file"}
        EVENTI_CARICAMENTO.append(evento)
        self.json({'documenti': len(INDICE.documenti), 'brani': len(INDICE.brani), 'ms': evento['ms']})

    def api_rimuovi(self, corpo):
        d = INDICE.documenti.get(corpo.get('id', ''))
        if not d or d['origine'] != 'caricato':
            raise ValueError('Si possono rimuovere solo i file caricati con +')
        percorso = (PROGETTO / d['percorso']).resolve()
        base = CARTELLA_CARICATI.resolve()
        if percorso.is_relative_to(base):
            percorso.unlink(missing_ok=True)
            cartella = percorso.parent                  # via anche le cartelle rimaste vuote (non caricati/ stessa)
            while cartella != base and cartella.is_relative_to(base) and cartella.exists() and not any(cartella.iterdir()):
                cartella.rmdir()
                cartella = cartella.parent
        INDICE.ricarica()
        INDICE.calcola_embedding()
        self.json({'documenti': len(INDICE.documenti)})

    def api_svuota_caricati(self):
        """Toglie tutti i file aggiunti con + o con la cartella. Se all'avvio si è indicata una cartella con --dati
        e si trova dentro caricati/, quella resta."""
        t = time.perf_counter()
        base = CARTELLA_CARICATI.resolve()
        dati = Path(cfg.CARTELLA_DATI).resolve() if cfg.CARTELLA_DATI else None
        tolti = 0
        if base.exists():
            for p in sorted(base.rglob('*'), key=lambda p: len(p.parts), reverse=True):   # prima i file, poi le cartelle
                if dati and (p == dati or p.is_relative_to(dati) or dati.is_relative_to(p)):
                    continue                     # la cartella dati e le cartelle che la contengono restano
                if p.is_file() or p.is_symlink():
                    p.unlink()
                    tolti += 1
                elif p.is_dir() and not any(p.iterdir()):
                    p.rmdir()
        INDICE.ricarica()
        INDICE.calcola_embedding()
        EVENTI_CARICAMENTO.append({'nome': 'carica_file', 'ms': ms(t), 'dettaglio': f'svuotati i caricati: {tolti} file tolti'})
        self.json({'tolti': tolti, 'documenti': len(INDICE.documenti), 'ms': ms(t)})

    def api_chiedi(self, corpo):
        domanda = (corpo.get('domanda') or '').strip()
        if not domanda:
            raise ValueError('Domanda vuota')
        modello = corpo.get('modello') or STATO['modello']
        precedenti = [v for v in STORICO.voci if not v.get('errore') and not v.get('meta')][-2:]
        # cronologia = gli scambi visibili in chat (la pagina manda i loro id); senza, le ultime domande dello storico
        per_id = {v['id']: v for v in STORICO.voci}
        vista = corpo.get('vista')
        cronologia = [per_id[i] for i in vista if i in per_id] if isinstance(vista, list) else STORICO.voci[-6:]
        cronologia = [v for v in cronologia if not v.get('errore')]
        with BLOCCO_TURNO:          # una nuova domanda interrompe l'estrazione degli elementi in corso
            TURNO['n'] += 1
            mio_turno = TURNO['n']

        self.send_response(200)
        self.send_header('Content-Type', 'application/x-ndjson; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()

        def invia(evento):
            self.wfile.write((json.dumps(evento, ensure_ascii=False) + '\n').encode())
            self.wfile.flush()

        t0 = time.perf_counter()
        strumenti = []
        diretta = motore.domanda_sulla_chat(domanda, cronologia)   # "cosa ti ho appena chiesto?": niente modello
        riassunto = False
        # seguito («e lui?»): la domanda si rende autonoma prima di pertinenza, ricerca e risposta; nello storico resta quella scritta
        domanda_ricerca = domanda if diretta else motore.riscrivi_domanda(modello, domanda, cronologia, strumenti, STATO['demo'])
        if diretta:
            conversazione = True
            strumenti.append({'nome': 'rispondi_dalla_cronologia', 'ms': ms(t0),
                              'dettaglio': f'domanda sulla chat ({diretta[1] if isinstance(diretta[1], str) else str(diretta[1]) + " indietro"}): '
                                           f'risposta presa da {len(cronologia)} scambi visibili'})
        elif motore.chiede_dei_documenti(domanda_ricerca):    # file sospetti o estranei: sempre nei documenti
            conversazione = False
            strumenti.append({'nome': 'controlla_pertinenza', 'ms': ms(t0),
                              'dettaglio': 'domanda sui documenti (sospetti o estranei) · regole'})
        else:
            conversazione = motore.controlla_pertinenza(INDICE, modello, domanda_ricerca, strumenti, STATO['demo'])
            riassunto = conversazione and bool(motore.RE_RIASSUNTO.search(motore._norma(domanda)))
        fonti = [] if conversazione else motore.cerca_fonti(INDICE, domanda, precedenti, strumenti, modello, STATO['demo'],
                                                            riscritta=domanda_ricerca if domanda_ricerca != domanda else None)
        ricerca_ms = ms(t0)
        for s in strumenti:
            invia({'tipo': 'strumento', **s})
        invia({'tipo': 'fonti', 'fonti': fonti})

        messaggi = motore.costruisci_messaggi(domanda if conversazione else domanda_ricerca, fonti, precedenti, conversazione,
                                              cronologia=[v for v in cronologia if not v.get('meta')],
                                              elementi_verificati=self.riepilogo_verificato() if riassunto else '',
                                              nota_archivio='' if conversazione else motore.note_per_prompt(INDICE, domanda_ricerca))

        risposta, prima_parola, stat, errore = '', None, {}, None
        t_gen = time.perf_counter()
        try:
            for tipo, valore in motore.genera(modello, messaggi, STATO['demo'], conversazione,
                                              testo_fisso=diretta[0] if diretta else None, lunga=riassunto):
                if tipo == 'token':
                    if prima_parola is None:
                        prima_parola = ms(t0)
                    risposta += valore
                    invia({'tipo': 'token', 'testo': valore})
                else:
                    stat = valore
        except BrokenPipeError:      # il browser ha chiuso la pagina
            return
        except urllib.error.HTTPError as e:
            errore = (f'Il modello "{modello}" non è scaricato. Nel terminale: ollama pull {modello}' if e.code == 404
                      else f'Errore di Ollama ({e.code}): {e.read().decode(errors="ignore")[:300]}')
        except (urllib.error.URLError, ConnectionError) as e:
            errore = ('Ollama non risponde: non è installato oppure non è avviato. '
                      'Installalo da ollama.com, aprilo e riprova.')
        except (RuntimeError, OSError) as e:
            errore = f'Errore del modello: {e}'
        gen_ms = ms(t_gen)
        limite_token = cfg.NUM_PREDICT_CONVERSAZIONE if conversazione and not riassunto else cfg.NUM_PREDICT
        strumenti.append({'nome': 'genera_risposta', 'ms': gen_ms,
                          'dettaglio': errore or ('dalla cronologia, senza modello' if diretta
                                                  else f'{modello}: {stat.get("token", 0)} token'
                                                       + (' · TRONCATA dal limite di token' if stat.get('token', 0) >= limite_token else ''))})

        t = time.perf_counter()
        persone = INDICE.persone_in(risposta) if risposta else []
        strumenti.append({'nome': 'estrai_persone', 'ms': ms(t), 'dettaglio': ', '.join(persone) or 'nessuna'})
        for s in strumenti[-2:]:
            invia({'tipo': 'strumento', **s})

        tok_s = round(stat['token'] / stat['gen_s'], 1) if stat.get('gen_s') else None
        voce = {
            'id': uuid.uuid4().hex[:10], 'ora': time.strftime('%Y-%m-%d %H:%M:%S'), 'domanda': domanda,
            'risposta': risposta, 'modello': modello if not STATO['demo'] else 'demo', 'rieseguita_da': corpo.get('rieseguita_da'),
            'fonti': [{k: f[k] for k in ('doc', 'n', 'punteggio')} | {'estratto': f['testo'][:300]} for f in fonti],
            'tempi': {'ricerca_ms': ricerca_ms, 'prima_parola_ms': prima_parola, 'totale_ms': ms(t0),
                      'token': stat.get('token'), 'token_prompt': stat.get('prompt_token'), 'tok_s': tok_s},
            'strumenti': strumenti, 'persone': persone, 'errore': errore,
        }
        if diretta:
            voce['meta'] = diretta[1]    # risposta sulla chat: non conta come domanda nella cronologia
        STORICO.aggiungi(voce)
        try:
            invia({'tipo': 'fine', 'voce': voce})
        except OSError:
            pass                     # pagina chiusa: estraiamo comunque gli elementi
        if errore or not risposta or conversazione:   # un saluto non ha luoghi, indizi o eventi
            return

        # ---- dopo la risposta: luoghi, indizi, eventi (fuori dal tempo misurato)
        t = time.perf_counter()
        nella_risposta = chiave_id(risposta)
        # solo i documenti del caso possono confermare luoghi, indizi ed eventi: la consultazione non è una prova
        fonti_caso = [f for f in fonti if not f.get('consultazione')]
        citate = [f for f in fonti_caso if chiave_id(f['doc']) in nella_risposta
                  or (f.get('id_interno') and chiave_id(f['id_interno']) in nella_risposta)] or fonti_caso[:3]
        indizi, eventi = estrai_voci_nominate(risposta, fonti_caso)   # nomi scritti dal modello, verificati nei documenti
        luoghi, origine = [], 'regole'
        if ESTRAZIONE_CON_MODELLO and not STATO['demo']:
            try:
                dati = estrai_con_modello(modello, citate, domanda, risposta, lambda: TURNO['n'] != mio_turno)
                if dati is None:
                    origine = 'regole (interrotta da una nuova domanda)'
                else:
                    luoghi = [verifica_citazione(e, citate) for e in dati.get('luoghi', []) if e.get('nome')]
                    indizi = [verifica_citazione(e, citate) for e in dati.get('indizi', []) if e.get('titolo')]
                    eventi = [verifica_citazione(e, citate) for e in dati.get('eventi', []) if e.get('titolo')]
                    origine = 'modello'
            except Exception as e:      # il modello non è riuscito: restano le regole
                origine = f'regole (modello non riuscito: {e})'
        noti = {chiave(x['nome']) for x in luoghi}
        # luoghi: scritti nei documenti E nominati nella risposta
        luoghi += [x for x in estrai_luoghi('\n'.join(f['testo'] for f in citate), persone)
                   if chiave(x['nome']) not in noti and chiave(x['nome']) in chiave(risposta)]
        # nella colonna destra va solo ciò che ha un riscontro nei documenti
        con_riscontro = lambda elenco: [e for e in elenco if e.get('verificato', True)]
        elementi = {'luoghi': con_riscontro(luoghi), 'indizi': con_riscontro(indizi), 'eventi': con_riscontro(eventi),
                    'origine': origine}
        strumento = {'nome': 'estrai_elementi', 'ms': ms(t),
                     'dettaglio': f'{len(luoghi)} luoghi, {len(indizi)} indizi, {len(eventi)} eventi · {origine}'}
        STORICO.imposta_elementi(voce, elementi, strumento)
        try:
            invia({'tipo': 'strumento', **strumento})
            invia({'tipo': 'elementi', 'id': voce['id'], 'elementi': elementi})
        except OSError:
            pass


def main():
    global INDICE, STORICO, VOLTI
    ap = argparse.ArgumentParser(description='Investigatore LAZZARO - interfaccia locale')
    ap.add_argument('--demo', action='store_true', help='nessun modello: risposte finte per provare l\'interfaccia')
    ap.add_argument('--modello', default=cfg.MODELLO)
    ap.add_argument('--embedding', default=cfg.MODELLO_EMBEDDING, help='modello Ollama per la ricerca semantica')
    ap.add_argument('--porta', type=int, default=PORTA)
    ap.add_argument('--apri', action='store_true', help='apre il browser quando il server è pronto')
    ap.add_argument('--dati', help='cartella di documenti da leggere a ogni avvio (di solito nessuna: si caricano con + o con la cartella)')
    args = ap.parse_args()
    STATO.update(modello=args.modello, demo=args.demo)
    cfg.MODELLO_EMBEDDING = args.embedding
    # Nessun documento caricato in automatico: si caricano a mano a ogni sessione (pulsanti + e cartella).
    # Solo con --dati una cartella viene letta a ogni avvio.
    if args.dati:
        cfg.CARTELLA_DATI = Path(args.dati).resolve()
        print(f'Cartella documenti letta a ogni avvio: {cfg.CARTELLA_DATI}')
    else:
        print('Nessun documento caricato in automatico: aggiungili con + o con il pulsante cartella.')

    CARTELLA_REGISTRO.mkdir(exist_ok=True)
    INDICE = Indice(embedding_attivo=not args.demo)
    INDICE.ricarica()
    STORICO = Storico(demo=args.demo, nome_doc=lambda d: INDICE.documenti.get(d, {}).get('nome', d))
    VOLTI = Volti()
    print(f'Documenti: {len(INDICE.documenti)}  brani: {len(INDICE.brani)}  '
          '(documenti pertinenti e persone si calcolano dopo gli embedding: vedi la riga "Pertinenza")')
    if not args.demo:
        threading.Thread(target=INDICE.calcola_embedding, daemon=True).start()
        threading.Thread(target=precarica, args=(args.modello,), daemon=True).start()

    # Solo questo computer: 127.0.0.1 e, per i browser che provano prima IPv6, anche ::1.
    # Niente riuso della porta: su Windows permetterebbe a due server di occuparla insieme.
    ThreadingHTTPServer.allow_reuse_address = False
    try:
        server = ThreadingHTTPServer(('127.0.0.1', args.porta), Gestore)
    except OSError:
        print(f'\nLa porta {args.porta} è già occupata: probabilmente l\'interfaccia è già avviata.')
        print(f'Apri http://127.0.0.1:{args.porta} oppure chiudi l\'altra finestra e riprova.')
        if args.apri:
            webbrowser.open(f'http://127.0.0.1:{args.porta}')
        return 1

    class ServerIPv6(ThreadingHTTPServer):
        address_family = socket.AF_INET6
    try:
        server6 = ServerIPv6(('::1', args.porta), Gestore)
        threading.Thread(target=server6.serve_forever, daemon=True).start()
    except OSError:
        pass   # IPv6 non disponibile: basta 127.0.0.1

    url = f'http://127.0.0.1:{args.porta}'
    print(f'Interfaccia pronta: {url}  {"(modalità demo)" if args.demo else "modello " + args.modello}')
    print('Lascia aperta questa finestra; per fermare premi Ctrl+C.')
    if args.apri:   # il browser si apre solo quando il server è già in ascolto
        threading.Timer(0.3, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    sys.exit(main())

