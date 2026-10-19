"""I tre passi di una risposta. Per cambiare il modo di rispondere (RAG, prompt, modello) si tocca qui:
il server e la pagina funzionano finché `genera` produce gli stessi eventi.

    fonti    = cerca_fonti(indice, domanda, precedenti, strumenti)   # lista di {doc, n, testo, punteggio}
    messaggi = costruisci_messaggi(domanda, fonti, precedenti)       # lista di {role, content} per Ollama
    for tipo, valore in genera(modello, messaggi, demo): ...         # ('token', testo) ... ('fine', statistiche)
"""
import config as cfg
from indice import contesto_da_fonti
from modello import genera_demo, genera_ollama
from testo import parole


def cerca_fonti(indice, domanda, precedenti, strumenti):
    """Sceglie i brani da dare al modello. `strumenti` si riempie con {nome, ms, dettaglio} di ogni passo."""
    # solo le domande di seguito molto corte ("e cosa dice?") cercano anche con la domanda precedente:
    # per le altre la domanda precedente porterebbe fuori strada la ricerca
    breve = len(parole(domanda)) <= 3
    query = f'{domanda} {precedenti[-1]["domanda"]}' if precedenti and breve else domanda
    return indice.cerca(query, strumenti)


def costruisci_messaggi(domanda, fonti, precedenti):
    messaggi = [{'role': 'system', 'content': cfg.istruzioni_correnti()}]
    for v in precedenti[-cfg.MEMORIA_TURNI:] if cfg.MEMORIA_TURNI else []:
        messaggi += [{'role': 'user', 'content': v['domanda']}, {'role': 'assistant', 'content': v['risposta']}]
    messaggi.append({'role': 'user', 'content': f'Fonti:\n{contesto_da_fonti(fonti)}\nDomanda: {domanda}'})
    return messaggi


def genera(modello, messaggi, demo=False):
    return (genera_demo if demo else genera_ollama)(modello, messaggi)
