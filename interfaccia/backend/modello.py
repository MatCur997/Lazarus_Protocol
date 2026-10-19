"""Parlare con Ollama: generazione della risposta in streaming, estrazione di luoghi/indizi/eventi, embedding."""
import json
import re
import time
import urllib.error
import urllib.request

import config as cfg
from config import ISTRUZIONI_ELEMENTI, SCHEMA_ELEMENTI
from testo import chiave, frasi, parole

# ------------------------------------------------------------------------- ollama
def ollama(percorso, corpo=None, timeout=600):
    dati = json.dumps(corpo).encode() if corpo is not None else None
    req = urllib.request.Request(cfg.OLLAMA + percorso, data=dati, headers={'Content-Type': 'application/json'})
    return urllib.request.urlopen(req, timeout=timeout)


def embed(testi):
    with ollama('/api/embed', {'model': cfg.MODELLO_EMBEDDING, 'input': testi, 'keep_alive': -1}, timeout=120) as r:
        return json.loads(r.read())['embeddings']


def modelli_disponibili():
    try:
        with ollama('/api/tags', timeout=2) as r:
            return [m['name'] for m in json.loads(r.read()).get('models', [])], True
    except Exception:
        return [], False


def precarica(modello):
    """Carica il modello in memoria e ce lo lascia (keep_alive=-1): la prima domanda non paga il caricamento."""
    try:
        with ollama('/api/generate', {'model': modello, 'keep_alive': -1}, timeout=300) as r:
            r.read()
    except Exception as e:
        print(f'  Precaricamento di {modello} non riuscito: {e}')


def apri_chat(corpo):
    try:
        return ollama('/api/chat', corpo)
    except urllib.error.HTTPError as e:
        if e.code == 400 and 'think' in e.read().decode(errors='ignore'):
            corpo.pop('think')            # modello senza modalità di ragionamento
            return ollama('/api/chat', corpo)
        raise


def genera_ollama(modello, messaggi, num_predict=None):
    """Generatore di eventi: ('token', testo) ... ('fine', statistiche)."""
    corpo = {'model': modello, 'messages': messaggi, 'stream': True, 'keep_alive': -1, 'think': False,
             'options': {'temperature': cfg.TEMPERATURA, 'num_ctx': cfg.NUM_CTX,
                         'num_predict': num_predict or cfg.NUM_PREDICT}}
    risposta = apri_chat(corpo)
    with risposta:
        for riga in risposta:
            if not riga.strip():
                continue
            pezzo = json.loads(riga)
            if 'error' in pezzo:
                raise RuntimeError(pezzo['error'])
            testo = pezzo.get('message', {}).get('content', '')
            if testo:
                yield 'token', testo
            if pezzo.get('done'):
                yield 'fine', {'token': pezzo.get('eval_count', 0),
                               'gen_s': pezzo.get('eval_duration', 0) / 1e9,
                               'prompt_token': pezzo.get('prompt_eval_count', 0)}


def estrai_con_modello(modello, fonti, domanda, risposta, annullato):
    """Chiede al modello luoghi/indizi/eventi in JSON. Ritorna None se annullata da una nuova domanda."""
    testo_fonti = '\n\n'.join(f'[{f["doc"]}] {f["testo"]}' for f in fonti)
    corpo = {'model': modello, 'stream': True, 'keep_alive': -1, 'think': False, 'format': SCHEMA_ELEMENTI,
             'options': {'temperature': 0, 'num_ctx': cfg.NUM_CTX, 'num_predict': 500},
             'messages': [{'role': 'system', 'content': ISTRUZIONI_ELEMENTI},
                          {'role': 'user', 'content': f'Fonti:\n{testo_fonti}\n\nDomanda: {domanda}\nRisposta: {risposta}'}]}
    testo = ''
    with apri_chat(corpo) as r:          # chiudere la connessione ferma la generazione in Ollama
        for riga in r:
            if annullato():
                return None
            if riga.strip():
                pezzo = json.loads(riga)
                if 'error' in pezzo:
                    raise RuntimeError(pezzo['error'])
                testo += pezzo.get('message', {}).get('content', '')
    return json.loads(testo)


def verifica_citazione(elemento, fonti):
    """La citazione deve esistere davvero nel documento: altrimenti si cerca la frase più simile."""
    citazione = elemento.get('citazione', '')
    candidati = [f for f in fonti if f['doc'] == elemento.get('doc')] or fonti
    c = chiave(citazione)
    for f in candidati:
        for frase in frasi(f['testo']):
            k = chiave(frase)
            if c and (c in k or k in c):
                return {**elemento, 'doc': f['doc'], 'citazione': frase, 'verificato': True}
    cercate = set(parole(citazione))
    migliore, punteggio = None, 0.0
    for f in candidati:
        for frase in frasi(f['testo']):
            p = set(parole(frase))
            s = len(cercate & p) / max(1, len(cercate))
            if s > punteggio:
                migliore, punteggio = (f['doc'], frase), s
    if migliore and punteggio >= 0.6:
        return {**elemento, 'doc': migliore[0], 'citazione': migliore[1], 'verificato': True}
    return {**elemento, 'verificato': False}


def genera_demo(_modello, messaggi):
    """Finto modello: riassume il primo brano trovato. Serve solo a provare l'interfaccia."""
    fonti = re.findall(r'\[([^\]]+)\] (.+?)(?=\n\[|\nDomanda:)', messaggi[-1]['content'], re.S)
    if fonti:
        doc, testo = fonti[0]
        corpo = ' '.join(frasi(testo)[1:4]) or testo
        risposta = f'(modalità demo) Secondo [{doc}]: {corpo}'
    else:
        risposta = '(modalità demo) Non ho trovato fonti utili per questa domanda.'
    time.sleep(0.25)
    parti = re.findall(r'\S+\s*', risposta)
    for p in parti:
        time.sleep(0.02)
        yield 'token', p
    yield 'fine', {'token': len(parti), 'gen_s': 0.02 * len(parti), 'prompt_token': 0}

