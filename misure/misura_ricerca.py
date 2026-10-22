"""Misura la ricerca dei brani SENZA il modello di chat: i documenti giusti compaiono tra le fonti? In che posizione? Quanto ci mette?

    python misure/misura_ricerca.py                          # domande scritte a mano + domande generate dai codici dei documenti
    python misure/misura_ricerca.py --senza-embedding        # solo parole chiave
    python misure/misura_ricerca.py --modello gemma4:12b     # misura anche i seguiti ("e lui?") con e senza riscrittura della domanda
    python misure/misura_ricerca.py --imposta TOP_K=6        # prova un valore (cerca il nome in config, indice e motore)
    python misure/misura_ricerca.py --json misure/prima.json # salva il risultato per confrontarlo con una prova successiva

Serve Ollama con bge-m3 (gli embedding si calcolano la prima volta e restano in misure/registro_misure/).
Le domande a mano sono in misure/domande_ricerca.json (campo `attesi`: nomi di file o ID interni; `tutti`: true = servono tutti,
per le catene di documenti; `storia`: gli scambi precedenti, per i seguiti). Le domande generate chiedono di un codice (pratica, circuito, tesserino) che compare in pochi documenti:
sono pensate per i grandi archivi di documenti simili tra loro. Il programma dell'investigatore non viene modificato."""
import argparse
import json
import random
import re
import shutil
import sys
import time
from pathlib import Path

QUI = Path(__file__).resolve().parent
PROGETTO = QUI.parent
sys.path.insert(0, str(PROGETTO / 'interfaccia' / 'backend'))

import config as cfg  # noqa: E402
import indice as indice_mod  # noqa: E402
import motore  # noqa: E402

MODELLI_DOMANDE = ['Cosa risulta su {c}?', 'Che cosa è stato fatto riguardo a {c}?', 'Ci sono documenti che parlano di {c}?',
                   'Dimmi tutto quello che sai di {c}.']
RE_CODICE_GENERATO = re.compile(r'(?<![\w-])([A-Z]{1,4}-?\d+(?:-\d+)*)(?![\w])')


def imposta(coppie):
    """NOME=VALORE: il valore (numero, vero/falso o testo) si scrive dove il nome esiste già."""
    for coppia in coppie:
        nome, _, grezzo = coppia.partition('=')
        try:
            valore = json.loads(grezzo.lower() if grezzo.lower() in ('true', 'false') else grezzo)
        except ValueError:
            valore = grezzo
        dove = [m for m in (cfg, indice_mod, motore) if hasattr(m, nome)]
        if not dove:
            sys.exit(f'--imposta: il nome {nome} non esiste in config, indice o motore')
        for m in dove:
            setattr(m, nome, valore)
        print(f'  impostato {nome} = {valore!r} in {", ".join(m.__name__ for m in dove)}')


def domande_generate(indice, quante, seme):
    """Domande su codici che compaiono in 1-3 documenti, con attesi tutti i documenti che li contengono."""
    per_codice = {}
    for d in indice.documenti.values():
        for c in set(RE_CODICE_GENERATO.findall(d['testo'])):
            per_codice.setdefault(c, set()).add(d['id'])
    nomi_doc = set(indice.documenti)
    interni = {d.get('id_interno') for d in indice.documenti.values()}
    candidati = sorted(c for c, docs in per_codice.items() if 1 <= len(docs) <= 3 and c not in nomi_doc and c not in interni
                       and not c.startswith(('LX', 'ARCH', 'WIKI')) and any(ch.isdigit() for ch in c) and len(c) >= 4)
    caso = random.Random(seme)
    caso.shuffle(candidati)
    return [{'domanda': caso.choice(MODELLI_DOMANDE).format(c=c), 'attesi': sorted(per_codice[c]), 'tutti': False,
             'gruppo': 'generate', 'nota': f'codice {c}'} for c in candidati[:quante]]


def trovato(fonti, atteso):
    return [i for i, f in enumerate(fonti) if atteso in (f['doc'], f.get('id_interno'))]


def valuta(indice, caso, modello=None, riscrivi=False):
    """riscrivi: la domanda di seguito si rende autonoma col modello (come fa il server) prima di cercare."""
    strumenti = []
    t = time.perf_counter()
    domanda, riscritta = caso['domanda'], None
    if riscrivi:
        riscritta = motore.riscrivi_domanda(modello, domanda, caso.get('storia', []), strumenti, False)
        riscritta = riscritta if riscritta != domanda else None
    fonti = motore.cerca_fonti(indice, domanda, [], strumenti, None, True, riscritta=riscritta)      # come il server: la scritta è la principale
    domanda = riscritta or domanda
    ms = (time.perf_counter() - t) * 1000
    posizioni = {a: (trovato(fonti, a) or [None])[0] for a in caso['attesi']}
    presi = [p for p in posizioni.values() if p is not None]
    ok = (len(presi) == len(caso['attesi'])) if caso.get('tutti') else bool(presi)
    return {'domanda': caso['domanda'] + (f'  →  {domanda}' if domanda != caso['domanda'] else ''), 'gruppo': caso.get('gruppo', 'a mano'), 'ok': ok, 'richiamo': len(presi) / len(caso['attesi']),
            'posizione': (min(presi) + 1) if presi else None, 'fonti': len(fonti), 'caratteri': sum(len(f['testo']) for f in fonti),
            'ms': round(ms), 'mancanti': [a for a, p in posizioni.items() if p is None], 'nota': caso.get('nota', '')}


def riepilogo(risultati):
    righe = []
    for gruppo in sorted({r['gruppo'] for r in risultati}):
        rs = [r for r in risultati if r['gruppo'] == gruppo]
        mrr = sum(1 / r['posizione'] for r in rs if r['posizione']) / len(rs)
        righe.append(f'{gruppo:10} domande {len(rs):3}  trovate {sum(r["ok"] for r in rs):3} ({100 * sum(r["ok"] for r in rs) / len(rs):5.1f}%)  '
                     f'richiamo {100 * sum(r["richiamo"] for r in rs) / len(rs):5.1f}%  MRR {mrr:.3f}  '
                     f'fonti {sum(r["fonti"] for r in rs) / len(rs):4.1f}  caratteri {sum(r["caratteri"] for r in rs) / len(rs):6.0f}  '
                     f'{sum(r["ms"] for r in rs) / len(rs):6.0f} ms')
    return '\n'.join(righe)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--cartella', default=str(PROGETTO / 'interfaccia' / 'caricati'), help='documenti da indicizzare')
    ap.add_argument('--registro', default=str(QUI / 'registro_misure'), help='dove stanno gli embedding di questa misura')
    ap.add_argument('--domande', default=str(QUI / 'domande_ricerca.json'))
    ap.add_argument('--generate', type=int, default=40, help='quante domande generate dai codici (0 = nessuna)')
    ap.add_argument('--seme', type=int, default=7)
    ap.add_argument('--senza-embedding', action='store_true')
    ap.add_argument('--modello', help='modello Ollama per riscrivere i seguiti (senza, i seguiti si misurano solo senza riscrittura)')
    ap.add_argument('--imposta', nargs='*', default=[], metavar='NOME=VALORE')
    ap.add_argument('--json', help='salva i risultati in questo file')
    ap.add_argument('--dettaglio', action='store_true', help='stampa anche le domande riuscite')
    args = ap.parse_args()

    registro = Path(args.registro)
    registro.mkdir(parents=True, exist_ok=True)
    vecchia_cache = PROGETTO / 'interfaccia' / 'registro' / 'cache_embedding.json'
    if vecchia_cache.exists() and not (registro / vecchia_cache.name).exists() and not any(registro.glob('cache_embedding*')):
        shutil.copy(vecchia_cache, registro / vecchia_cache.name)      # riparte dagli embedding già calcolati dall'app
    for m in (cfg, indice_mod):
        m.CARTELLA_CARICATI, m.CARTELLA_REGISTRO = Path(args.cartella), registro
    imposta(args.imposta)

    t = time.perf_counter()
    indice = indice_mod.Indice(embedding_attivo=not args.senza_embedding)
    indice.ricarica()
    print(f'{len(indice.documenti)} documenti, {len(indice.brani)} brani letti in {time.perf_counter() - t:.1f} s')
    if not args.senza_embedding:
        t = time.perf_counter()
        indice.calcola_embedding()
        print(f'embedding e pertinenza: {time.perf_counter() - t:.0f} s · semantica attiva: {indice.embedding_ok} · {indice.info_pertinenza.get("dettaglio")}')

    casi = [{**c, 'gruppo': c.get('gruppo', 'a mano')} for c in json.loads(Path(args.domande).read_text(encoding='utf-8'))['domande']]
    presenti = set(indice.documenti) | {d.get('id_interno') for d in indice.documenti.values()}
    saltate = [c for c in casi if not all(a in presenti for a in c['attesi'])]
    casi = [c for c in casi if c not in saltate]
    if saltate:
        print(f'{len(saltate)} domande a mano saltate: i documenti attesi non sono nella cartella')
    if args.generate:
        casi += domande_generate(indice, args.generate, args.seme)
    risultati = []
    for c in casi:
        if c.get('storia'):         # seguiti: prima come li cercherebbe un sistema senza memoria, poi con la domanda resa autonoma
            risultati.append({**valuta(indice, c), 'gruppo': 'seguiti senza riscrittura'})
            if args.modello:
                risultati.append({**valuta(indice, c, args.modello, True), 'gruppo': 'seguiti con riscrittura'})
        else:
            risultati.append(valuta(indice, c))
    for r in risultati:
        if args.dettaglio or not r['ok']:
            print(f'{"OK    " if r["ok"] else "MANCA "} pos {str(r["posizione"]):>4} richiamo {r["richiamo"]:.2f} {r["ms"]:5} ms  [{r["gruppo"]}] {r["domanda"]}'
                  + (f'  mancanti: {", ".join(r["mancanti"])}' if r['mancanti'] else ''))
    print('\n' + riepilogo(risultati))
    if args.json:
        Path(args.json).write_text(json.dumps({'impostazioni': args.imposta, 'risultati': risultati}, ensure_ascii=False, indent=1), encoding='utf-8')


if __name__ == '__main__':
    main()
