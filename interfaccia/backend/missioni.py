"""Missioni dei database: parametri, regole, ipotesi sulle tabelle e piano a passi, una voce per file (missioni.json).

I parametri che la centrale aggiorna durante la prova ("nuovo istante", "canale del veicolo") si salvano in
registro/missioni_parametri.json con la loro storia; missioni.json non si tocca. Ogni interrogazione del tool SQL
finisce in registro/missione_log.jsonl con i parametri in vigore: serve a consegnare query e risultati e a vedere
cosa cambia ripetendo lo stesso test dopo un aggiornamento."""
import hashlib
import json
import re
import threading
import time
import unicodedata
from pathlib import Path

QUI = Path(__file__).resolve().parent
FILE_MISSIONI = QUI / 'missioni.json'
REGISTRO = QUI.parent / 'registro'
FILE_PARAMETRI = REGISTRO / 'missioni_parametri.json'
FILE_LOG = REGISTRO / 'missione_log.jsonl'
_LOCK = threading.Lock()


def _leggi(percorso, vuoto):
    try:
        return json.loads(percorso.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return vuoto


def _norma(testo):
    """Minuscolo, senza accenti e senza punteggiatura (il trattino resta: V-46). Senza togliere gli accenti «può» non era «puo»
    e le domande risolutive scritte con l'accento non avviavano il piano a passi."""
    t = ''.join(c for c in unicodedata.normalize('NFD', (testo or '').lower()) if unicodedata.category(c) != 'Mn')
    return re.sub(r'\s+', ' ', re.sub(r"[^\w\s-]", ' ', t)).strip()


def missione(nome_db):
    """La missione di un database con i parametri in vigore (quelli aggiornati dalla centrale hanno la precedenza).
    Un database senza voce in missioni.json ha una missione vuota: il tool funziona lo stesso, senza regole."""
    base = _leggi(FILE_MISSIONI, {}).get(nome_db, {})
    agg = _leggi(FILE_PARAMETRI, {}).get(nome_db, {})
    m = {'titolo': nome_db, 'parole': [], 'regole': [], 'ipotesi': {}, 'passi': [], 'verdetto': None, **base}
    m['parametri'] = {**base.get('parametri', {}), **agg.get('parametri', {})}
    m['storia_parametri'] = agg.get('storia', [])
    m['database'] = nome_db
    return m


def missione_per_tabelle(tabelle):
    """Il nome del database (voce di missioni.json) le cui tabelle ipotizzate sono tutte in `tabelle`, o None. Serve ai CSV caricati senza il
    loro database (TABELLE_CSV: "gli stessi dati, con gli stessi nomi"): il database ricostruito prende quel nome e quindi quella missione."""
    tabelle = set(tabelle)
    for nome, voce in _leggi(FILE_MISSIONI, {}).items():
        ipotesi = voce.get('ipotesi') if isinstance(voce, dict) else None
        if ipotesi and set(ipotesi) <= tabelle:
            return nome
    return None


def compila(testo, parametri):
    """'{istante}' -> valore del parametro; un parametro senza valore diventa 'NON INDICATO (chiedere alla centrale)'."""
    return re.sub(r'\{(\w+)\}', lambda m: str(parametri[m[1]]) if parametri.get(m[1]) not in (None, '')
                  else 'NON INDICATO (chiedere alla centrale)' if m[1] in parametri else m[0], testo)


def regole_testo(m):
    return '\n'.join(f'- {compila(r, m["parametri"])}' for r in m.get('regole', []))


def punteggio(m, domanda, valori=()):
    """Quanto la domanda riguarda questa missione: parole chiave della missione e valori del database nominati."""
    q = _norma(domanda)
    punti = sum(1 for p in m.get('parole', []) if _norma(p) and _norma(p) in q)
    punti += sum(1 for v in m['parametri'].values() if isinstance(v, str) and len(v) >= 3 and _norma(v) in q)
    punti += sum(2 for v in valori if len(v) >= 4 and re.search(rf'(?<![\w-]){re.escape(_norma(v))}(?![\w])', q))
    return punti


def e_risolutiva(m, domanda):
    """Domanda che chiede la soluzione (operatori autorizzati, terminale e chiave): si usa il piano a passi."""
    return bool(m.get('passi')) and bool(m.get('risolutiva')) and bool(re.search(m['risolutiva'], _norma(domanda)))


# ------------------------------------------------------------------ aggiornamenti della centrale
RE_ISTANTE = re.compile(r'\b(\d{4}-\d{2}-\d{2})[ t](\d{1,2}):(\d{2})\b')
RE_ORA = re.compile(r'\b(?:alle|ore|orario)\s+(\d{1,2})[:.](\d{2})\b')
RE_VARCO = re.compile(r'\bvarco\s+([a-z]-?\d+(?:-[a-z0-9]+)?)\b', re.I)
RE_CANALE = re.compile(r'\b[Cc]anale\s+([A-Z][\w-]+)')     # "Canale Delta" (nome con la maiuscola)
RE_AGGIORNAMENTO = re.compile(r'\b(centrale|aggiorn\w*|nuov[oiae]|cambi\w*|modific\w*|parametr\w*|imposta|usa|veicolo)\b', re.I)


def aggiornamento_richiesto(domanda, m, valori=()):
    """{parametro: nuovo valore} se il messaggio comunica un aggiornamento dei parametri della missione
    ("la centrale aggiorna: istante 2026-11-04 12:30, canale del veicolo Delta"). Vuoto se non è un aggiornamento."""
    if not RE_AGGIORNAMENTO.search(domanda) or not m.get('parametri') or '?' in domanda:
        return {}       # una domanda ("il veicolo usa il canale Omega?") non è una comunicazione della centrale
    nuovi, p = {}, m['parametri']
    if 'istante' in p:
        t = RE_ISTANTE.search(domanda)
        if t:
            nuovi['istante'] = f'{t[1]} {int(t[2]):02d}:{t[3]}'
        elif (o := RE_ORA.search(domanda)) and p.get('istante'):
            nuovi['istante'] = f'{p["istante"][:10]} {int(o[1]):02d}:{o[2]}'
    if 'varco' in p and (v := RE_VARCO.search(domanda)):
        nuovi['varco'] = v[1].upper()
    if 'canale_veicolo' in p and re.search(r'\bcanale\b', domanda, re.I):
        # prima i canali che esistono nel database ("Canale Delta": basta "Delta" nella frase), poi "Canale <Nome>"
        noti = [v for v in valori if str(v).lower().startswith('canale ')]
        trovato = next((v for v in noti if re.search(rf'\b{re.escape(v.split(" ", 1)[1])}\b', domanda, re.I)), None)
        if not trovato and (c := RE_CANALE.search(domanda)):
            trovato = f'Canale {c[1]}'
        if trovato:
            nuovi['canale_veicolo'] = trovato
    return {k: v for k, v in nuovi.items() if p.get(k) != v}


def aggiorna_parametri(nome_db, nuovi, origine='chat'):
    """Salva i nuovi parametri con la storia (prima -> dopo, quando, da dove). Ritorna le modifiche fatte."""
    with _LOCK:
        tutti = _leggi(FILE_PARAMETRI, {})
        voce = tutti.setdefault(nome_db, {'parametri': {}, 'storia': []})
        attuali = missione(nome_db)['parametri']
        modifiche = {k: {'prima': attuali.get(k), 'dopo': v} for k, v in nuovi.items() if attuali.get(k) != v}
        if modifiche:
            voce['parametri'].update({k: v['dopo'] for k, v in modifiche.items()})
            voce['storia'].append({'quando': time.strftime('%Y-%m-%d %H:%M:%S'), 'origine': origine, 'modifiche': modifiche})
            REGISTRO.mkdir(exist_ok=True)
            FILE_PARAMETRI.write_text(json.dumps(tutti, ensure_ascii=False, indent=1), encoding='utf-8')
        return modifiche


def ripristina_parametri(nome_db):
    """Torna ai parametri di missioni.json (la storia resta, con una voce di ripristino)."""
    with _LOCK:
        tutti = _leggi(FILE_PARAMETRI, {})
        voce = tutti.setdefault(nome_db, {'parametri': {}, 'storia': []})
        if voce['parametri']:
            voce['storia'].append({'quando': time.strftime('%Y-%m-%d %H:%M:%S'), 'origine': 'ripristino',
                                   'modifiche': {k: {'prima': v, 'dopo': None} for k, v in voce['parametri'].items()}})
            voce['parametri'] = {}
            REGISTRO.mkdir(exist_ok=True)
            FILE_PARAMETRI.write_text(json.dumps(tutti, ensure_ascii=False, indent=1), encoding='utf-8')


# ------------------------------------------------------------------ registro delle interrogazioni
def firma_risultato(r):
    """Impronta del risultato (righe di ogni passo e sintesi): due esecuzioni con la stessa impronta hanno dato lo stesso esito."""
    passi = r.get('passi') or [r]
    dati = [[p.get('colonne'), p.get('righe')] for p in passi] + [r.get('sintesi', '')]
    return hashlib.sha1(json.dumps(dati, ensure_ascii=False, default=str).encode()).hexdigest()[:12]


def registra(nome_db, domanda, risultato, risposta=''):
    """Una riga nel registro della missione. Se la stessa domanda era già stata fatta, dice se il risultato è cambiato."""
    m = missione(nome_db)
    voce = {'id': hashlib.sha1(f'{time.time()}{domanda}'.encode()).hexdigest()[:10], 'quando': time.strftime('%Y-%m-%d %H:%M:%S'),
            'database': nome_db, 'parametri': m['parametri'], 'domanda': domanda, 'firma': firma_risultato(risultato),
            'sintesi': risultato.get('sintesi', ''), 'errore': risultato.get('errore'),
            'passi': [{'scopo': p.get('scopo', 'query'), 'sql': p.get('sql'), 'colonne': p.get('colonne', []),
                       'righe': p.get('righe', [])[:30], 'errore': p.get('errore'), 'tentativi': len(p.get('tentativi', []))}
                      for p in (risultato.get('passi') or [risultato])],
            'risposta': risposta[:3000]}
    precedente = next((v for v in reversed(leggi_log()) if v['domanda'] == domanda and v['database'] == nome_db), None)
    if precedente:
        voce['ripete'] = precedente['id']
        voce['cambiato'] = precedente['firma'] != voce['firma']
        voce['parametri_cambiati'] = {k: [precedente['parametri'].get(k), v] for k, v in voce['parametri'].items()
                                      if precedente['parametri'].get(k) != v}
    with _LOCK:
        REGISTRO.mkdir(exist_ok=True)
        with FILE_LOG.open('a', encoding='utf-8') as f:
            f.write(json.dumps(voce, ensure_ascii=False, default=str) + '\n')
    return voce


def leggi_log(limite=None):
    try:
        righe = [json.loads(r) for r in FILE_LOG.read_text(encoding='utf-8').splitlines() if r.strip()]
    except (OSError, ValueError):
        righe = []
    return righe[-limite:] if limite else righe
