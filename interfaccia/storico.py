"""Storico delle domande (registro/storico.jsonl), elementi estratti ed esclusioni."""
import json
import threading

import config as cfg
from config import CARTELLA_REGISTRO
from testo import chiave

# ------------------------------------------------------------------------ storico
class Storico:
    def __init__(self, demo=False, nome_doc=lambda d: d):
        self.lock = threading.Lock()
        self.nome_doc = nome_doc      # id documento -> nome del file, per diario_chat.jsonl
        self.demo = demo
        self.file = CARTELLA_REGISTRO / ('storico_demo.jsonl' if demo else 'storico.jsonl')
        self.file_elementi = CARTELLA_REGISTRO / ('elementi_demo.jsonl' if demo else 'elementi.jsonl')
        self.file_esclusi = CARTELLA_REGISTRO / 'elementi_esclusi.txt'
        self.voci = []
        self.esclusi = set()
        if self.file.exists():
            for riga in self.file.read_text(encoding='utf-8').splitlines():
                try:
                    self.voci.append(json.loads(riga))
                except ValueError:
                    pass
        per_id = {v['id']: v for v in self.voci}
        if self.file_elementi.exists():
            for riga in self.file_elementi.read_text(encoding='utf-8').splitlines():
                try:
                    r = json.loads(riga)
                except ValueError:
                    continue
                if r.get('id') in per_id:
                    per_id[r['id']]['elementi'] = r['elementi']
                    per_id[r['id']].setdefault('strumenti', []).append(r['strumento'])
        if self.file_esclusi.exists():
            self.esclusi = {r.strip() for r in self.file_esclusi.read_text(encoding='utf-8').splitlines() if r.strip()}

    def imposta_elementi(self, voce, elementi, strumento):
        with self.lock:
            voce['elementi'] = elementi
            voce.setdefault('strumenti', []).append(strumento)
            with self.file_elementi.open('a', encoding='utf-8') as f:
                f.write(json.dumps({'id': voce['id'], 'elementi': elementi, 'strumento': strumento}, ensure_ascii=False) + '\n')

    def elimina(self, voce_id=None):
        """Toglie una domanda dallo storico (o tutte se voce_id è None) e riscrive i file del registro.
        diario_chat.jsonl (registro della squadra) non viene toccato."""
        with self.lock:
            prima = len(self.voci)
            self.voci = [] if voce_id is None else [v for v in self.voci if v['id'] != voce_id]
            def originale(v):   # elementi e relativo strumento stanno in elementi.jsonl: non vanno duplicati qui
                return {k: ([s for s in x if s.get('nome') != 'estrai_elementi'] if k == 'strumenti' else x)
                        for k, x in v.items() if k != 'elementi'}
            self.file.write_text(''.join(json.dumps(originale(v), ensure_ascii=False) + '\n' for v in self.voci), encoding='utf-8')
            if self.file_elementi.exists():
                tenuti = []
                for riga in self.file_elementi.read_text(encoding='utf-8').splitlines():
                    try:
                        if json.loads(riga).get('id') in {v['id'] for v in self.voci}:
                            tenuti.append(riga + '\n')
                    except ValueError:
                        pass
                self.file_elementi.write_text(''.join(tenuti), encoding='utf-8')
            return prima - len(self.voci)

    def escludi(self, tipo, k):
        with self.lock:
            self.esclusi.add(f'{tipo}|{k}')
            self.file_esclusi.write_text('\n'.join(sorted(self.esclusi)), encoding='utf-8')

    def raccogli(self, tipo):
        """Luoghi/indizi/eventi di tutte le risposte, uniti per titolo. Ritorna {chiave: elemento}."""
        campo = 'nome' if tipo == 'luoghi' else 'titolo'
        raccolta = {}
        for v in self.voci:
            visti = set()
            for e in (v.get('elementi') or {}).get(tipo, []):
                k = chiave(e.get(campo, ''))
                if not k or k in visti or f'{tipo}|{k}' in self.esclusi:
                    continue
                visti.add(k)
                r = raccolta.setdefault(k, {'chiave': k, 'nome': e[campo], 'citazioni': 0, 'fonti': [], 'risposte': [], 'ultima': v['ora']})
                r['citazioni'] += 1
                r['ultima'] = v['ora']
                r['risposte'].append(v['id'])
                if e.get('citazione'):
                    fonte = {'doc': e.get('doc'), 'citazione': e['citazione'], 'verificato': e.get('verificato', False)}
                    if fonte not in r['fonti']:
                        r['fonti'].append(fonte)
        return raccolta

    def aggiungi(self, voce):
        with self.lock:
            self.voci.append(voce)
            CARTELLA_REGISTRO.mkdir(exist_ok=True)
            with self.file.open('a', encoding='utf-8') as f:
                f.write(json.dumps(voce, ensure_ascii=False) + '\n')
        # stesso formato del notebook, così diario_chat.jsonl resta il registro della squadra
        if self.demo or voce.get('errore'):
            return
        try:
            with cfg.DIARIO_NOTEBOOK.open('a', encoding='utf-8') as f:
                f.write(json.dumps({'domanda': voce['domanda'], 'risposta': voce['risposta'],
                                    'fonti': sorted({self.nome_doc(x['doc']) for x in voce['fonti']})},
                                   ensure_ascii=False) + '\n')
        except OSError:
            pass

