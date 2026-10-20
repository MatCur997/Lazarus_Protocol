"""I passi di una risposta. Per cambiare il modo di rispondere (RAG, prompt, modello) si tocca qui:
il server e la pagina funzionano finché `genera` produce gli stessi eventi.

    conversazione = controlla_pertinenza(indice, modello, domanda, strumenti)   # non sul caso? niente documenti
    fonti    = cerca_fonti(indice, domanda, precedenti, strumenti)   # lista di {doc, n, testo, punteggio}
    messaggi = costruisci_messaggi(domanda, fonti, precedenti, conversazione)  # lista di {role, content} per Ollama
    for tipo, valore in genera(modello, messaggi, demo, conversazione): ...   # ('token', testo) ... ('fine', statistiche)
"""
import time

import config as cfg
from indice import contesto_da_fonti
from modello import genera_demo, genera_ollama
from testo import ms, parole


def turni_sul_caso(precedenti):
    """Scambi che hanno letto documenti: solo questi si ricordano sul caso, così una risposta evasiva non si propaga."""
    if not cfg.MEMORIA_TURNI:
        return []
    return [v for v in precedenti if v.get('fonti') and v.get('risposta')][-cfg.MEMORIA_TURNI:]


def controlla_pertinenza(indice, modello, domanda, strumenti, demo=False, precedenti=()):
    """Vero se il messaggio non riguarda il caso: in quel caso non si cercano fonti e si conversa liberamente.
    Il filtro vede anche la domanda precedente se quel turno era sul caso: "e a che ora?" ne è un seguito."""
    t = time.perf_counter()
    with indice.lock:
        titoli = [f"{d['id']}: {d.get('titolo', '')}" for d in indice.documenti.values()]
        nomi = list(indice.persone) + list(indice.alias)
    turno = turni_sul_caso(list(precedenti)[-1:])
    conversazione, motivo = cfg.classifica_domanda(domanda, modello, titoli, nomi, demo,
                                                   turno[0]['domanda'] if turno else '')
    if turno:
        motivo += ' · col turno precedente'
    strumenti.append({'nome': 'controlla_pertinenza', 'ms': ms(t),
                      'dettaglio': f'{"conversazione: documenti non letti" if conversazione else "domanda sul caso"} · {motivo}'})
    return conversazione


def cerca_fonti(indice, domanda, precedenti, strumenti):
    """Sceglie i brani da dare al modello. `strumenti` si riempie con {nome, ms, dettaglio} di ogni passo."""
    # solo le domande di seguito molto corte ("e cosa dice?") cercano anche con la domanda precedente:
    # per le altre la domanda precedente porterebbe fuori strada la ricerca
    breve = len(parole(domanda)) <= 3
    query = f'{domanda} {precedenti[-1]["domanda"]}' if precedenti and breve else domanda
    return indice.cerca(query, strumenti)


def costruisci_messaggi(domanda, fonti, precedenti, conversazione=False):
    def coppie(turni):
        return [m for v in turni for m in ({'role': 'user', 'content': v['domanda']},
                                           {'role': 'assistant', 'content': v['risposta'][:cfg.MAX_CARATTERI_RISPOSTA_MEMORIA]})]

    if conversazione:      # niente documenti: il contesto è libero, si ricordano gli ultimi scambi di qualunque tipo
        storia = [v for v in precedenti if v.get('risposta')][-cfg.MEMORIA_TURNI_CONVERSAZIONE:]
        sistema = cfg.istruzioni_conversazione(con_storia=bool(storia))
        return [{'role': 'system', 'content': sistema}, *coppie(storia), {'role': 'user', 'content': domanda}]
    messaggi = [{'role': 'system', 'content': cfg.istruzioni_correnti()}, *coppie(turni_sul_caso(precedenti))]
    messaggi.append({'role': 'user', 'content': f'Fonti:\n{contesto_da_fonti(fonti)}\nDomanda: {domanda}'})
    return messaggi


def genera(modello, messaggi, demo=False, conversazione=False):
    if demo:
        return genera_demo(modello, messaggi)
    return genera_ollama(modello, messaggi, cfg.NUM_PREDICT_CONVERSAZIONE if conversazione else None)
