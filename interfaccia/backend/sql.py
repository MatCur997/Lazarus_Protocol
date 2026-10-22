"""Tool SQL di sola lettura sui database caricati (accessi_g_4.sqlite, archivio_recuperato.sqlite, qualunque .sqlite).

Il modello propone query di lettura in JSON {"sql": "SELECT ..."}, il codice le esegue con un controllo che vieta
qualunque scrittura, e il risultato torna al modello che risponde citando le fonti. Oltre al notebook Tool_SQL_g_4:
- profilo del database: conteggi, valori distinti, intervalli di date, campioni e relazioni DEDOTTE confrontando i
  valori delle chiavi (l'archivio recuperato non ha vincoli né nomi leggibili: r03.k0, m12.k1...);
- missione del database (missioni.py): parametri aggiornabili dalla centrale, regole, ipotesi sulle tabelle;
- esiti calcolati dal codice: intervalli [inizio, fine) con fine NULL = senza scadenza, revoche efficaci dall'istante
  di effetto, stati. Il modello sceglie le righe, il calcolo delle date non lo fa lui;
- piano a passi per le domande risolutive (terminali, permessi, persone, finestre e chiavi), con gli esclusi."""
import csv
import hashlib
import json
import re
import sqlite3
import time
from pathlib import Path

import config as cfg
import missioni
from modello import apri_chat

ESTENSIONI_DATABASE = {'.sqlite', '.sqlite3', '.db'}
TENTATIVI = 3
SQL_TOKEN = 1200        # con 600 le query con molte JOIN (tutte le regole del varco) restavano troncate: JSON non valido
MAX_RIGHE = 50
MAX_RIGHE_PASSO = 300   # un passo del piano deve vedere TUTTI i candidati (persone delle squadre: ~100 righe)
MAX_RISPOSTA = 10        # righe di un elenco SENZA filtri: oltre è un'estrazione in blocco, non un'indagine
FUNZIONI_PERMESSE = {'count', 'min', 'max', 'lower', 'upper', 'length', 'coalesce', 'sum', 'avg', 'trim', 'substr',
                     'replace', 'date', 'datetime', 'ifnull', 'abs', 'round', 'instr', 'like', 'group_concat',
                     'strftime', 'julianday', 'iif', 'nullif', 'typeof', 'total',
                     # di sola lettura anche queste: senza, GLOB, rtrim o time() erano rifiutati come se fossero scritture
                     'glob', 'ltrim', 'rtrim', 'printf', 'format', 'time', 'unixepoch', 'char', 'unicode', 'hex'}
# domande che chiedono di cambiare i DATI: il database è in sola lettura, si risponde senza proporre SQL.
# Serve un oggetto (database, tabella, permesso...): "la centrale ha aggiornato i parametri" non è una scrittura.
RE_SCRITTURA = re.compile(
    r'\b(drop|delete|update|insert|alter|truncate)\b\s+(table|from|into|\w+\s+set)'
    r'|\b(cancell|elimin|rimuov|modific|aggiorn|inserisc|aggiung|svuot|sovrascriv|altera|forza)\w*\b.{0,40}?'
    r'\b(database|db|tabell\w*|dat[io]\b|rig[ah]e?\b|record|permess\w*|codic\w*|valor\w*|accessi|autorizzazion\w*|'
    r'abilitazion\w*|revoc\w*|sospension\w*|stato|chiav\w*)', re.I)
RE_PARTICIPIO = re.compile(r'(?:at|ut|it)[oaie]$')


def chiede_di_scrivere(domanda):
    """Vero se il messaggio chiede di CAMBIARE i dati («cancella i permessi», «modifica lo stato», DROP TABLE). Un verbo al participio
    parla di una modifica già avvenuta: «chi ha aggiornato lo stato del permesso?» è una domanda sui dati, non una scrittura
    (prima veniva rifiutata). Il database resta comunque in sola lettura: questo controllo decide solo la risposta."""
    for m in RE_SCRITTURA.finditer(domanda):
        if m[1] or not RE_PARTICIPIO.search(re.match(r'\w+', m[0])[0].lower()):     # comando SQL, o verbo non al participio
            return True
    return False


RE_ORDINE_ARBITRARIO = re.compile(r'\border\s+by\b[^;]*\blimit\s+(?:0\s*,\s*)?1\b', re.I | re.S)    # anche LIMIT 0, 1
RE_MAPPA = re.compile(r'\b(mappa|dizionario|struttura|schema)\b.{0,30}\b(tabell\w*|database|dati|archivio)\b'
                      r'|\b(tabell\w*)\b.{0,20}\b(significat\w*|relazion\w*|collegament\w*)\b'
                      # "quali tabelle ci sono nel database?", "elenco delle tabelle": la risposta è lo schema, non una query sui dati.
                      # Solo "tabelle" al plurale subito dopo: «la tabella e10 ha revoche per Ardesia?» è una domanda sui DATI
                      r'|\b(quali|quante|che)\s+tabelle\b|\bsignifica\w*\b.{0,20}\btabell\w*'
                      r'|\b(elenc\w*|lista)\s+(?:del\w*\s+|le\s+)?tabelle\b', re.I)


def e_database(percorso):
    """Vero se il file è un database SQLite (dall'intestazione, non solo dall'estensione)."""
    try:
        with open(percorso, 'rb') as f:
            return f.read(16) == b'SQLite format 3\x00'
    except OSError:
        return False


def _connetti(percorso):
    con = sqlite3.connect(Path(percorso).resolve().as_uri() + '?mode=ro', uri=True)
    con.execute('PRAGMA query_only=ON')
    return con


_CACHE_DESCRIZIONE = {}


def _firma(percorso):
    p = Path(percorso)
    return str(p.resolve()), p.stat().st_mtime_ns


def descrivi(percorso):
    """{tabelle: {nome: [colonne]}, collegamenti: [...], righe: {nome: n}} letti dal database. Si ricorda finché il file non cambia:
    `interroga_database` la chiedeva a ogni query (un conteggio di tutte le tabelle per ogni tentativo del modello)."""
    chiave = _firma(percorso)
    if chiave not in _CACHE_DESCRIZIONE:
        for vecchia in [k for k in _CACHE_DESCRIZIONE if k[0] == chiave[0]]:    # una voce per database: ogni scrittura
            del _CACHE_DESCRIZIONE[vecchia]                                      # ne aggiungeva una nuova, per sempre
        _CACHE_DESCRIZIONE[chiave] = _descrivi(percorso)
    return _CACHE_DESCRIZIONE[chiave]


def _descrivi(percorso):
    con = _connetti(percorso)
    try:
        tabelle, collegamenti, righe = {}, [], {}
        for (nome,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"):
            tabelle[nome] = [c[1] for c in con.execute(f'PRAGMA table_info("{nome}")')]
            righe[nome] = con.execute(f'SELECT count(*) FROM "{nome}"').fetchone()[0]
            for fk in con.execute(f'PRAGMA foreign_key_list("{nome}")'):
                collegamenti.append(f'{nome}.{fk[3]} collega {fk[2]}.{fk[4] or "id"}')
        return {'tabelle': tabelle, 'collegamenti': collegamenti, 'righe': righe}
    finally:
        con.close()


# ------------------------------------------------------------------ profilo (la "mappa" con le prove)
_CACHE_PROFILO = {}
RE_DATA = re.compile(r'^\d{4}-\d{2}-\d{2}( \d{2}:\d{2}(:\d{2})?)?$')


def profila(percorso):
    """Profilo di tutto il database, non di un campione: per ogni tabella righe, colonne (tipo, distinti, NULL,
    minimo/massimo delle date, valori frequenti delle etichette, campione) e le relazioni. Le relazioni dichiarate
    vengono dai vincoli; quelle DEDOTTE dal confronto dei valori: una colonna intera i cui valori (tutti, non un
    campione) sono chiavi primarie di un'altra tabella la collega a quella. copertura = quota di valori trovati."""
    p = Path(percorso)
    chiave = (str(p.resolve()), p.stat().st_mtime_ns)
    if chiave in _CACHE_PROFILO:
        return _CACHE_PROFILO[chiave]
    con = _connetti(percorso)
    try:
        nomi = [n for (n,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        tabelle, chiavi_primarie, dichiarate = [], {}, []
        for t in nomi:
            info = list(con.execute(f'PRAGMA table_info("{t}")'))
            pk = [c[1] for c in info if c[5]]
            if len(pk) == 1:
                chiavi_primarie[t] = (pk[0], {v for (v,) in con.execute(f'SELECT "{pk[0]}" FROM "{t}"')})
            for fk in con.execute(f'PRAGMA foreign_key_list("{t}")'):
                dichiarate.append((t, fk[3], fk[2], fk[4] or 'id'))
            n = con.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
            colonne = []
            for c in info:
                nome, tipo = c[1], (c[2] or '').upper()
                distinti, nulli = con.execute(f'SELECT count(DISTINCT "{nome}"), sum("{nome}" IS NULL) FROM "{t}"').fetchone()
                col = {'nome': nome, 'tipo': tipo, 'pk': bool(c[5]), 'non_nullo': bool(c[3]), 'distinti': distinti, 'null': nulli or 0}
                esempio = con.execute(f'SELECT "{nome}" FROM "{t}" WHERE "{nome}" IS NOT NULL LIMIT 1').fetchone()
                if esempio and isinstance(esempio[0], str) and RE_DATA.match(esempio[0]):
                    col['data'] = True
                    col['min'], col['max'] = con.execute(f'SELECT min("{nome}"), max("{nome}") FROM "{t}"').fetchone()
                elif 'TEXT' in tipo and distinti <= 12:
                    col['valori'] = [[v, k] for v, k in con.execute(
                        f'SELECT "{nome}", count(*) FROM "{t}" GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 12')]
                colonne.append(col)
            campione = [list(r) for r in con.execute(f'SELECT * FROM "{t}" LIMIT 2')]
            tabelle.append({'nome': t, 'righe': n, 'colonne': colonne, 'campione': campione})
        relazioni = []
        for t in tabelle:
            for c in t['colonne']:
                dich = next(((a, b) for (x, y, a, b) in dichiarate if x == t['nome'] and y == c['nome']), None)
                if c['pk'] and not dich:
                    continue
                if not dich and 'INT' not in c['tipo']:
                    continue
                valori = {v for (v,) in con.execute(f'SELECT DISTINCT "{c["nome"]}" FROM "{t["nome"]}" WHERE "{c["nome"]}" IS NOT NULL')}
                if not valori:
                    continue
                candidati = [(len(valori & chiavi_primarie[u][1]) / len(valori), u) for u in chiavi_primarie if u != t['nome']]
                if dich:
                    cop = len(valori & chiavi_primarie.get(dich[0], (None, set()))[1]) / len(valori)
                    relazioni.append({'da': t['nome'], 'colonna': c['nome'], 'a': dich[0], 'a_colonna': dich[1],
                                      'copertura': round(cop, 3), 'dichiarata': True, 'valori': len(valori)})
                elif candidati:
                    cop, u = max(candidati)
                    if cop >= 0.9:
                        relazioni.append({'da': t['nome'], 'colonna': c['nome'], 'a': u, 'a_colonna': chiavi_primarie[u][0],
                                          'copertura': round(cop, 3), 'dichiarata': False, 'valori': len(valori)})
        for r in relazioni:     # ogni colonna sa a cosa punta (per lo schema e per il modello)
            col = next(c for t in tabelle if t['nome'] == r['da'] for c in t['colonne'] if c['nome'] == r['colonna'])
            col['fk'] = {'tabella': r['a'], 'colonna': r['a_colonna'], 'copertura': r['copertura'], 'dedotta': not r['dichiarata']}
        profilo = {'tabelle': tabelle, 'relazioni': relazioni}
    finally:
        con.close()
    for vecchia in [k for k in _CACHE_PROFILO if k[0] == chiave[0]]:     # una voce per database: con due database caricati usati a
        del _CACHE_PROFILO[vecchia]                                          # turno il profilo non si ricalcola a ogni cambio
    _CACHE_PROFILO[chiave] = profilo
    return profilo


def valori_etichetta(percorso):
    """Valori di testo dei campi "etichetta" (pochi distinti o nomi): servono a capire quale database nomina la domanda."""
    valori = set()
    for t in profila(percorso)['tabelle']:
        for c in t['colonne']:
            valori.update(str(v) for v, _ in c.get('valori', []) if v)
    return valori


def _riga_colonna(c):
    pezzi = [c['nome'], (c['tipo'] or '?').lower()]
    if c['pk']:
        pezzi.append('PK')
    if c.get('fk'):
        f = c['fk']
        pezzi.append(f'→ {f["tabella"]}.{f["colonna"]}' + (f' (dedotta, {f["copertura"]:.0%} dei valori)' if f['dedotta'] else ''))
    if c.get('data'):
        pezzi.append(f'date {c["min"]} … {c["max"]}')
    if c.get('valori'):
        pezzi.append('valori: ' + ', '.join(f'{v} ×{k}' for v, k in c['valori'][:8]))
    elif not c['pk']:
        pezzi.append(f'{c["distinti"]} distinti')
    if c['null']:
        pezzi.append(f'NULL {c["null"]}')
    return ' '.join(pezzi[:2]) + (' · ' + ' · '.join(pezzi[2:]) if len(pezzi) > 2 else '')


def schema_testo(percorso, m=None):
    """Lo schema per il modello: profilo di ogni tabella (con le ipotesi della squadra) e le regole della missione."""
    m = m or missioni.missione(Path(percorso).name)
    righe = []
    for t in profila(percorso)['tabelle']:
        ipotesi = m.get('ipotesi', {}).get(t['nome'])
        righe.append(f'{t["nome"]} ({t["righe"]} righe){" — ipotesi: " + ipotesi if ipotesi else ""}')
        righe += [f'  {_riga_colonna(c)}' for c in t['colonne']]
    # le uguaglianze da usare nelle JOIN, scritte per intero: con le sole frecce accanto alle colonne il modello collegava due chiavi
    # esterne che puntano a tabelle diverse (e10.k1 = r08.k1)
    relazioni = profila(percorso)['relazioni']
    if relazioni:
        righe += ['', 'COLLEGAMENTI (nelle JOIN usa esattamente queste uguaglianze):']
        righe += [f'  {r["da"]}.{r["colonna"]} = {r["a"]}.{r["a_colonna"]}' for r in relazioni]
    regole = missioni.regole_testo(m)
    return '\n'.join(righe) + (f'\n\nREGOLE DELLA MISSIONE:\n{regole}' if regole else '')


def struttura(percorso):
    """Per lo schema logico (pagina Schema SQL): profilo, relazioni dichiarate e dedotte, ipotesi e missione."""
    m = missioni.missione(Path(percorso).name)
    p = profila(percorso)
    return {'tabelle': p['tabelle'], 'relazioni': p['relazioni'], 'ipotesi': m.get('ipotesi', {}),
            'funzioni': sorted(FUNZIONI_PERMESSE), 'max_righe': MAX_RISPOSTA, 'tentativi': TENTATIVI,
            'regole': [missioni.compila(r, m['parametri']) for r in m.get('regole', [])],
            'parametri': m['parametri'], 'storia_parametri': m.get('storia_parametri', []), 'titolo': m.get('titolo'),
            'passi': [missioni.compila(x['scopo'], m['parametri']) for x in m.get('passi', [])],
            'momento_missione': m['parametri'].get('istante', '')}


# ------------------------------------------------------------------ esecuzione
# nomi delle tabelle definite nella query: WITH [RECURSIVE] nome [(colonne)] AS (...), nome2 AS (...)
RE_CTE = re.compile(r'(?:\bwith(?:\s+recursive)?|,)\s*"?(\w+)"?\s*(?:\([^()]*\))?\s*as\s*(?:not\s+)?(?:materialized\s*)?\(', re.I)


def interroga_database(query, percorso, massimo=MAX_RIGHE):
    """Esegue UNA query di sola lettura. Ritorna {colonne, righe, altre_righe} oppure {errore}."""
    if not isinstance(query, str) or not query.strip():
        return {'errore': 'Scrivi una query SQL.'}
    if not Path(percorso).is_file():
        return {'errore': 'Carica il database della squadra.'}
    tabelle = set(descrivi(percorso)['tabelle'])
    cte = {n.lower() for n in RE_CTE.findall(query) if not n.lower().startswith('sqlite_')} - {t.lower() for t in tabelle}
    con = _connetti(percorso)
    negati = []     # perché il controllo ha rifiutato: sqlite dice solo "not authorized", il modello deve sapere cosa correggere

    def autorizza(azione, arg1, arg2, db, trigger):
        if azione == sqlite3.SQLITE_SELECT:
            return sqlite3.SQLITE_OK
        if azione == sqlite3.SQLITE_READ:
            # anche le tabelle definite dalla query stessa (WITH RECURSIVE n ...), e solo quelle: non le tabelle di sistema
            if arg1 in tabelle or (arg1 or '').lower() in cte:
                return sqlite3.SQLITE_OK
            negati.append(f'la tabella {arg1} non è leggibile (tabelle: {", ".join(sorted(tabelle))})')
            return sqlite3.SQLITE_DENY
        if azione == sqlite3.SQLITE_FUNCTION:
            if (arg2 or '').lower() in FUNZIONI_PERMESSE:
                return sqlite3.SQLITE_OK
            negati.append(f'la funzione {arg2} non è permessa (permesse: {", ".join(sorted(FUNZIONI_PERMESSE))})')
            return sqlite3.SQLITE_DENY
        if azione == getattr(sqlite3, 'SQLITE_RECURSIVE', -1):      # WITH ... (tabelle temporanee della query)
            return sqlite3.SQLITE_OK
        negati.append('sono permesse solo letture (SELECT): scritture, PRAGMA e ATTACH sono vietati')
        return sqlite3.SQLITE_DENY          # qualunque scrittura, PRAGMA, ATTACH...: vietato
    con.set_authorizer(autorizza)
    passi = 0

    def limite():                            # niente query infinite
        nonlocal passi
        passi += 1
        return 1 if passi > 5000 else 0
    con.set_progress_handler(limite, 1000)
    try:
        cursore = con.execute(query)
        if cursore.description is None:      # solo commenti o ';': prima description None faceva cadere tutto il tool
            return {'errore': 'nessuna SELECT da eseguire: scrivi una query SELECT'}
        righe = cursore.fetchmany(massimo + 1)
        return {'colonne': [x[0] for x in cursore.description], 'righe': [list(r) for r in righe[:massimo]],
                'altre_righe': len(righe) > massimo}
    except (sqlite3.Error, sqlite3.Warning) as e:
        if passi > 5000:
            negati.append('query troppo pesante, interrotta: restringi con WHERE e collega le tabelle con i COLLEGAMENTI dello schema')
        motivi = list(dict.fromkeys(negati))
        return {'errore': str(e) + (f' ({"; ".join(motivi)})' if motivi else '')}
    finally:
        con.close()


# ------------------------------------------------------------------ valori della query che il database non contiene
_CACHE_VALORI_DB = {}
RE_LETTERALE = re.compile(r"'((?:[^']|'')*)'")
RE_MODELLO = re.compile(r'\b(like|glob)\s*$', re.I)
RE_CONCATENATO = re.compile(r'\|\|\s*$')       # 'Cocito' in '%' || 'Cocito' || '%': un pezzo, non un valore


def valori_del_database(percorso, massimo_per_colonna=20000):
    """Tutti i valori del database come testo minuscolo (per sapere se un filtro della query cerca qualcosa che esiste)."""
    chiave = _firma(percorso)
    if chiave not in _CACHE_VALORI_DB:
        valori = set()
        con = _connetti(percorso)
        try:
            for tabella, colonne in descrivi(percorso)['tabelle'].items():
                for c in colonne:
                    valori.update(str(v).lower() for (v,) in con.execute(
                        f'SELECT DISTINCT "{c}" FROM "{tabella}" WHERE "{c}" IS NOT NULL LIMIT {massimo_per_colonna}'))
        finally:
            con.close()
        _CACHE_VALORI_DB.clear()
        _CACHE_VALORI_DB[chiave] = valori
    return _CACHE_VALORI_DB[chiave]


def valori_assenti(query, percorso):
    """I valori tra apici della query che non compaiono in nessuna colonna del database (né in maiuscolo né in minuscolo). Le date si
    confrontano con < e >, i modelli di LIKE e GLOB hanno jolly: quelli non contano. «PX9999» assente = 0 righe è la risposta giusta, non un errore.
    Il trattino basso conta fuori da LIKE: prima 'FUORI_SERVIZO' (sbagliato) non veniva segnalato perché conteneva '_'."""
    query = query or ''
    valori = valori_del_database(percorso)
    assenti = []
    for m in RE_LETTERALE.finditer(query):
        v = m[1].replace("''", "'")
        prima, dopo = query[:m.start()], query[m.end():]
        # il modello può essere dentro una funzione (LIKE lower('%x%')): il % basta a riconoscerlo
        if (len(v) < 2 or RE_DATA.match(v) or '%' in v or RE_MODELLO.search(prima)
                or RE_CONCATENATO.search(prima) or dopo.lstrip().startswith('||')):
            continue
        if v.lower() not in valori:
            assenti.append(v)
    return list(dict.fromkeys(assenti))


# ------------------------------------------------------------------ CSV caricati senza il loro database
RE_NOME_COLONNA = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
RE_NOME_CHIAVE = re.compile(r'^(k\d+|id|.*_id|id_.*)$', re.I)
RE_INTERO = re.compile(r'^-?(0|[1-9]\d*)$')       # "090788" NON è un intero: è un codice e resta testo con lo zero


def leggi_tabella_csv(percorso):
    """(colonne, righe) se il CSV è una tabella (intestazione con nomi di colonna, stesso numero di campi in ogni riga), altrimenti None."""
    try:
        with open(percorso, encoding='utf-8-sig', newline='') as f:
            righe = list(csv.reader(f))
    except (OSError, UnicodeDecodeError, csv.Error):
        return None
    if len(righe) < 2 or len(righe[0]) < 2 or not all(RE_NOME_COLONNA.match(c.strip()) for c in righe[0]):
        return None
    colonne = [c.strip() for c in righe[0]]
    dati = [r for r in righe[1:] if any(x.strip() for x in r)]
    if not dati or any(len(r) != len(colonne) for r in dati):
        return None
    return colonne, dati


def database_da_csv(csv_tabelle, destinazione):
    """Costruisce (o riusa, se i CSV non sono cambiati) un database SQLite con una tabella per CSV: {nome file: (colonne, righe)}.
    Tipi: una colonna con soli interi (senza zeri iniziali) è INTEGER se ha il nome di una chiave (k0, k1, id, *_id) o valori di lunghezza
    diversa; tutte cifre della stessa lunghezza (283618, 406288) è un codice e resta TEXT, come nel database originale («codici come testo»).
    Un campo vuoto è NULL. La prima colonna intera con
    valori tutti diversi (k0, id) è la chiave primaria: così il profilo deduce le relazioni come sul database originale."""
    destinazione = Path(destinazione)
    # sha1 e non hash(): hash() delle stringhe cambia a ogni avvio di Python, e il database veniva ricostruito a ogni riavvio
    firma = hashlib.sha1(json.dumps(sorted(csv_tabelle.items()), ensure_ascii=False).encode('utf-8')).hexdigest()
    file_firma = destinazione.with_suffix('.firma')
    if destinazione.exists() and file_firma.exists() and file_firma.read_text(encoding='utf-8') == firma:
        return destinazione
    destinazione.parent.mkdir(parents=True, exist_ok=True)
    provvisorio = destinazione.with_suffix('.tmp')
    provvisorio.unlink(missing_ok=True)
    con = sqlite3.connect(provvisorio)
    try:
        for nome, (colonne, righe) in sorted(csv_tabelle.items()):
            valori = [[(x.strip() if x.strip() != '' else None) for x in r] for r in righe]
            interi = []
            for i, c in enumerate(colonne):
                pieni = [r[i] for r in valori if r[i] is not None]
                cifre = bool(pieni) and all(RE_INTERO.match(v) for v in pieni)
                interi.append(cifre and (bool(RE_NOME_CHIAVE.match(c)) or len({len(v.lstrip('-')) for v in pieni}) > 1))
            chiave = next((i for i in range(len(colonne)) if interi[i] and i == 0
                           and len({r[i] for r in valori}) == len(valori) and None not in {r[i] for r in valori}), None)
            definizione = ', '.join(f'"{c}" ' + ('INTEGER PRIMARY KEY' if i == chiave else 'INTEGER' if interi[i] else 'TEXT')
                                    for i, c in enumerate(colonne))
            con.execute(f'CREATE TABLE "{nome}" ({definizione})')
            con.executemany(f'INSERT INTO "{nome}" VALUES ({", ".join("?" * len(colonne))})',
                            [[int(v) if interi[i] and v is not None else v for i, v in enumerate(r)] for r in valori])
        con.commit()
    finally:
        con.close()
    provvisorio.replace(destinazione)
    file_firma.write_text(firma, encoding='utf-8')
    return destinazione


# anche "FROM r08 p, e10 r": con le tabelle separate da virgola gli alias non si vedevano e la JOIN sbagliata passava.
# Le virgole dell'elenco di colonne non fanno danni: "p.k0" o "v1" non sono nomi di tabella.
RE_ALIAS = re.compile(r'(?:\b(?:from|join)\s+|,\s*)"?(\w+)"?(?:\s+(?:as\s+)?(?!(?:on|using|where|join|left|right|full|inner|outer|'
                      r'cross|natural|group|order|limit|union|except|intersect|having|window)\b)(\w+))?', re.I)
RE_UGUALE = re.compile(r'\b(\w+)\.(\w+)\s*=\s*(\w+)\.(\w+)\b')


def controlla_join(query, percorso):
    """Ogni uguaglianza a.x = b.y tra colonne chiave deve seguire le relazioni del profilo: una chiave esterna con la
    chiave primaria della tabella a cui punta, o due chiavi esterne verso la stessa tabella. Collegare due colonne che
    puntano a tabelle diverse (es. revoca.k1 -> permesso con permesso.k1 -> squadra) dà risultati sbagliati senza errori."""
    try:
        tabelle = {t['nome']: t for t in profila(percorso)['tabelle']}
    except Exception:
        return None
    alias = {}
    for t, a in RE_ALIAS.findall(query or ''):
        if t in tabelle:
            alias[t] = t
            if a:
                alias[a] = t

    def bersaglio(a, c):        # a quale tabella si riferisce il valore di a.c (None = non è una chiave)
        t = tabelle.get(alias.get(a, ''))
        col = next((x for x in t['colonne'] if x['nome'] == c), None) if t else None
        if not col:
            return None
        if col['pk']:
            return t['nome']
        return col['fk']['tabella'] if col.get('fk') else None

    def chiave_primaria(t):
        return next((x['nome'] for x in tabelle[t]['colonne'] if x['pk']), 'k0')

    for a1, c1, a2, c2 in RE_UGUALE.findall(query or ''):
        b1, b2 = bersaglio(a1, c1), bersaglio(a2, c2)
        if b1 and b2 and b1 != b2:
            t1, t2 = alias[a1], alias[a2]
            # la correzione precisa: con il messaggio generico gemma ripeteva la stessa JOIN in tutti e tre i tentativi
            # (e10.k1 = r08.k1 invece di e10.k1 = r08.k0: la revoca punta al permesso, non alla sua squadra)
            correzioni = ([f'{a2}.{c2} = {a1}.{chiave_primaria(t1)}'] if b2 == t1 else []) \
                + ([f'{a1}.{c1} = {a2}.{chiave_primaria(t2)}'] if b1 == t2 else [])
            return (f'JOIN incoerente: {a1}.{c1} = {a2}.{c2} collega {t1}.{c1}, che punta a {b1}, con {t2}.{c2}, che punta a {b2}. '
                    + (f'Scrivi invece: {" oppure ".join(correzioni)}.' if correzioni else
                       'Collega una chiave esterna con la chiave primaria della tabella a cui punta (vedi COLLEGAMENTI nello schema).'))
    return None


def controlla_query(query, percorso=None):
    """Errori di metodo, prima ancora di eseguire: scegliere "il primo" ordinando gli identificativi è vietato,
    e le JOIN devono seguire le relazioni (dichiarate o dedotte) del database."""
    if RE_ORDINE_ARBITRARIO.search(query or ''):
        return ('ORDER BY ... LIMIT 1 sceglie una riga solo per la sua posizione: è vietato dalla missione. '
                'Restituisci TUTTI i candidati che rispettano le regole, senza LIMIT 1.')
    return controlla_join(query, percorso) if percorso else None


def troppo_ampia(query, esito):
    """Elenco in blocco: tante righe senza nessun filtro né aggregazione ("dammi tutti i codici")."""
    q = (query or '').lower()
    filtrata = re.search(r'\b(where|group\s+by|having)\b', q) or re.search(r'\b(count|min|max|avg|sum|group_concat|total)\s*\(', q)
    return len(esito.get('righe', [])) > MAX_RISPOSTA and not filtrata


# ------------------------------------------------------------------ esiti calcolati (date, NULL, revoche, stati)
NEGATIVI = ('sosp', 'revoc', 'rev_', 'blocc')     # intervalli/eventi che ESCLUDONO quando sono attivi
STATI_VALIDI = {'attivo', 'operativo', 'ok', 'valido'}


def _data(x):
    """Data confrontabile come testo: '2026-11-04T12:00' e '2026-11-04 12:00:00' diventano '2026-11-04 12:00:00'. Senza, una
    data con i secondi era «dopo» la stessa senza secondi: un intervallo che inizia all'istante esatto risultava non ancora valido."""
    s = str(x).strip().replace('T', ' ')
    return s + ':00' if len(s) == 16 else s


def _attivo(inizio, fine, t):
    return inizio is not None and _data(inizio) <= _data(t) and (fine is None or _data(t) < _data(fine))


def _efficace(effetto, t):
    return effetto is not None and _data(effetto) <= _data(t)


def _canale(x):
    """'Canale Delta' e 'Delta' sono lo stesso canale."""
    return re.sub(r'^canale\s+', '', str(x).strip(), flags=re.I).lower()


def annota(colonne, righe, parametri, completo=False):
    """Aggiunge la colonna "esito" calcolata dal codice. Colonne riconosciute (alias scelti dal piano a passi):
    X_inizio / X_fine = intervallo [inizio, fine), fine NULL = senza scadenza; X_effetto = evento efficace da quell'istante;
    stato = deve essere attivo/operativo; canale = deve essere il canale del veicolo (se la centrale lo ha indicato).
    Gli intervalli di sospensioni e revoche escludono quando sono attivi; gli altri escludono quando NON lo sono.
    completo=False (query di esplorazione): solo se ci sono intervalli o eventi. Un conteggio per stato non ha esiti:
    marcare "ESCLUSO" la riga BLOCCATO faceva sparire quella riga dalla risposta."""
    t = parametri.get('istante')
    if not t or not righe:
        return colonne, righe
    nomi = [c.lower() for c in colonne]
    prefissi = {n[:-7] for n in nomi if n.endswith('_inizio') and n[:-7] + '_fine' in nomi}
    eventi = [n for n in nomi if n.endswith('_effetto')]
    stati = [n for n in nomi if n == 'stato' or n.endswith('_stato')]
    canale = parametri.get('canale_veicolo')
    if not (prefissi or eventi or (completo and (stati or ('canale' in nomi and canale)))):
        return colonne, righe
    nuove = []
    for r in righe:
        v = dict(zip(nomi, r))
        motivi = []
        for p in sorted(prefissi):
            ini, fin = v.get(p + '_inizio'), v.get(p + '_fine')
            negativo = p.startswith(NEGATIVI)
            if negativo:
                if _attivo(ini, fin, t):
                    motivi.append(f'{p} attiva ({ini} → {fin or "senza fine"})')
            elif ini is None:
                motivi.append(f'nessun {p}')
            elif not _attivo(ini, fin, t):
                motivi.append(f'{p} non valido a {t} ({ini} → {fin or "senza fine"})')
        for e in eventi:
            if _efficace(v.get(e), t):
                motivi.append(f'{e[:-8]} efficace dal {v[e]}')
        for s in stati:
            if v.get(s) is not None and str(v[s]).lower() not in STATI_VALIDI:
                motivi.append(f'{s} {v[s]}')
        if canale and 'canale' in v and v['canale'] is not None and _canale(v['canale']) != _canale(canale):
            motivi.append(f'canale {v["canale"]} diverso da {canale}')
        nuove.append(list(r) + ['VALIDO' if not motivi else 'ESCLUSO: ' + '; '.join(motivi)])
    return list(colonne) + ['esito'], nuove


def _bloccata(r, t):
    """Sospensione attiva o revoca efficace in QUESTA riga. Con due sospensioni (o due revoche) la stessa persona (o permesso)
    ha due righe: ne basta una bloccata per escluderla, la riga con l'altra sospensione non la rende valida."""
    for k, v in r.items():
        # serve anche la colonna _fine, come in annota: senza, annota ignora l'intervallo e la riga resterebbe VALIDO
        if (k.endswith('_inizio') and k[:-7].startswith(NEGATIVI) and k[:-7] + '_fine' in r
                and _attivo(v, r[k[:-7] + '_fine'], t)):
            return True
        if k.endswith('_effetto') and _efficace(v, t):
            return True
    return False


def _valori(colonne, righe, nome):
    nomi = [c.lower() for c in colonne]
    return [dict(zip(nomi, r)).get(nome) for r in righe]


def sintesi_varco(passi, m):
    """Conclusione calcolata dal codice sui quattro passi del varco: coppie SANITARIO + SICUREZZA della stessa squadra
    con permessi validi, terminali utilizzabili, finestre e chiavi valide. Più possibilità = non si sceglie: si dice."""
    ruoli = (m.get('verdetto') or {}).get('ruoli', ['SANITARIO', 'SICUREZZA'])
    par = m['parametri']
    if len(passi) < 4 or any(p.get('errore') for p in passi):
        return 'Piano incompleto: almeno un passo non ha dato risultati, la conclusione non è calcolabile.'
    righe = lambda i: [dict(zip([c.lower() for c in passi[i]['colonne']], r)) for r in passi[i]['righe']]
    valido = lambda r: str(r.get('esito', '')).startswith('VALIDO')
    # permessi e persone con più righe (una per revoca o sospensione): esclusi se una qualunque è bloccata
    revocati = {r.get('permesso'): r for r in righe(1) if r.get('permesso') is not None and _bloccata(r, par['istante'])}
    sospese = {r.get('persona'): r for r in righe(2) if r.get('persona') is not None and _bloccata(r, par['istante'])}
    # passo 2: squadre con un permesso valido per ogni ruolo
    per_squadra = {}
    for r in righe(1):
        if valido(r) and r.get('permesso') not in revocati:
            per_squadra.setdefault((r.get('squadra'), r.get('nome_squadra')), set()).add(str(r.get('ruolo')).upper())
    squadre = [s for s, rr in per_squadra.items() if all(x in rr for x in ruoli)]
    # la squadra nel passo 3 può arrivare come codice o come nome: si riporta sempre al codice del passo 2
    codice_di = {str(n): s for s, n in per_squadra} | {str(s): s for s, _ in per_squadra}
    for r in righe(1):
        codice_di.setdefault(str(r.get('nome_squadra')), r.get('squadra'))
        codice_di.setdefault(str(r.get('squadra')), r.get('squadra'))
    # passo 3: persone valide per ruolo dentro quelle squadre
    persone = {}
    esclusi = {}        # (squadra, ruolo) -> persone escluse con il motivo
    for r in righe(2):
        squadra = codice_di.get(str(r.get('squadra')), codice_di.get(str(r.get('nome_squadra')), r.get('squadra')))
        chiave = (squadra, str(r.get('ruolo')).upper())
        if valido(r) and r.get('persona') not in sospese:
            persone.setdefault(chiave, {})[r.get('persona')] = f'{r.get("nome")} ({r.get("badge")})'
        else:
            motivo = str(sospese.get(r.get('persona'), r).get('esito'))[9:]   # riga valida ma persona sospesa in un'altra riga
            esclusi.setdefault(chiave, []).append(f'{r.get("nome")} ({r.get("badge")}) escluso: {motivo}')
    coppie = []
    for s, nome in squadre:
        a, b = persone.get((s, ruoli[0]), {}), persone.get((s, ruoli[1]), {})
        coppie += [(nome or s, a[x], b[y]) for x in a for y in b if x != y]
    # passi 1 e 4: terminali operativi del varco con finestra e chiave valide
    terminali = {r.get('terminale'): r for r in righe(0)}
    finestre, chiavi = {}, {}
    for r in righe(3):
        term = r.get('terminale')
        if not (terminali.get(term) and valido(terminali[term])):
            continue
        if r.get('finestra_inizio') is not None and _attivo(r.get('finestra_inizio'), r.get('finestra_fine'), par['istante']):
            finestre[term] = True
        if r.get('chiave') is not None and _attivo(r.get('chiave_inizio'), r.get('chiave_fine'), par['istante']):
            chiavi.setdefault(term, set()).add(str(r.get('chiave')))
    utilizzabili = [(t, terminali[t].get('canale'), sorted(chiavi[t])) for t in chiavi if finestre.get(t)]
    righe_testo = [f'Istante {par.get("istante")}, varco {par.get("varco")}.']
    righe_testo.append(f'Squadre con permesso valido per {" e ".join(ruoli)}: '
                       + (', '.join(str(n or s) for s, n in squadre) or 'nessuna'))
    righe_testo.append('Coppie possibili (stessa squadra, persone diverse, abilitate e non sospese): '
                       + ('; '.join(f'{sq}: {a} + {b}' for sq, a, b in coppie) or 'nessuna'))
    righe_testo.append('Terminali del varco esclusi: ' + ('; '.join(f'{t} ({str(r.get("esito"))[9:]})'
                       for t, r in terminali.items() if not valido(r)) or 'nessuno'))
    righe_testo.append('Terminali utilizzabili con finestra e chiave valide: '
                       + ('; '.join(f'terminale {t} ({c}) chiave {", ".join(k)}' for t, c, k in utilizzabili) or 'nessuno'))
    # perché ogni squadra con un permesso per il varco va bene o no: serve a «perché la Calce no?» e «Hilda può da sola?»
    permessi = {}
    for r in righe(1):
        permessi.setdefault((r.get('squadra'), r.get('nome_squadra')), []).append(r)
    spiegazioni = []
    for (s, nome), rr in permessi.items():
        motivi = []
        for ruolo in ruoli:
            if ruolo not in per_squadra.get((s, nome), set()):
                del_ruolo = list(dict.fromkeys(str(revocati.get(r.get('permesso'), r).get('esito'))[9:] for r in rr
                                               if str(r.get('ruolo')).upper() == ruolo))
                motivi.append(f'permesso {ruolo} non valido ({"; ".join(del_ruolo) or "nessun permesso"})')
        if not motivi:
            a, b = persone.get((s, ruoli[0]), {}), persone.get((s, ruoli[1]), {})
            for ruolo, validi in ((ruoli[0], a), (ruoli[1], b)):
                if not validi:
                    motivi.append(f'nessun {ruolo} valido ('
                                  + ('; '.join(dict.fromkeys(esclusi.get((s, ruolo), []))) or 'nessun membro con questo ruolo') + ')')
            if a and b and not any(x != y for x in a for y in b):
                motivi.append(f'{next(iter(a.values()))} ha entrambi i ruoli ma è una sola persona: ne servono due diverse')
        spiegazioni.append(f'{nome or s}: ' + ('VALIDA' if not motivi else 'NO, ' + ' | '.join(motivi)))
    righe_testo.append('Verifica di ogni squadra con un permesso per il varco:\n' + '\n'.join(f'- {x}' for x in spiegazioni))
    if not par.get('canale_veicolo'):
        canali = sorted({str(c) for _, c, _ in utilizzabili})
        if len(canali) > 1:
            righe_testo.append(f'DATO MANCANTE: il canale del lettore del veicolo non è indicato e i terminali utilizzabili '
                               f'usano canali diversi ({", ".join(canali)}): chiedilo alla centrale prima di scegliere.')
    if not coppie or not utilizzabili:
        righe_testo.append('CONCLUSIONE: nessun accesso valido con questi parametri. Dichiaralo.')
    elif len(coppie) > 1 or len(utilizzabili) > 1 or any(len(k) > 1 for _, _, k in utilizzabili):
        righe_testo.append('CONCLUSIONE: restano più possibilità. Non sceglierne una per ordine: elencale, spiega cosa '
                           'manca per decidere e chiedilo alla centrale.')
    else:
        righe_testo.append('CONCLUSIONE: una sola soluzione valida.')
    return '\n'.join(righe_testo)


# ------------------------------------------------------------------ collegamento con i dossier
COLONNE_ESCLUSE = re.compile(r'^(id|.*_id|k\d+|codice|stato|valido_.*|data.*|.*_at|ts_.*)$', re.I)
_CACHE_VALORI = {}


def valori_citati(percorso, documenti, massimo_valori=3000):
    """Collegamento database <-> dossier: quali valori di testo del database (nomi, depositi, settori, pratiche...)
    compaiono davvero nei documenti caricati. I valori generici ("Operatore 021", "PX0306") non sono citati da nessuna
    parte; quelli del caso sì ("Luca Serra" in ARCH-3097). Le esche dei dossier citano valori assenti dal database.
    documenti: [(id, testo)]. Ritorna [{tabella, colonna, valore, documenti: [id...]}], i più citati prima.
    Il risultato resta in memoria finché database e documenti non cambiano (0,6 s su 1524 documenti a ogni domanda)."""
    p = Path(percorso)
    chiave = (str(p.resolve()), p.stat().st_mtime_ns, hash(tuple((d, len(t)) for d, t in documenti)))
    if chiave not in _CACHE_VALORI:
        if len(_CACHE_VALORI) > 4:
            _CACHE_VALORI.clear()
        _CACHE_VALORI[chiave] = _valori_citati(percorso, documenti, massimo_valori)
    return _CACHE_VALORI[chiave]


def _valori_citati(percorso, documenti, massimo_valori):
    con = _connetti(percorso)
    try:
        valori = []
        for tabella, colonne in descrivi(percorso)['tabelle'].items():
            for colonna in colonne:
                if COLONNE_ESCLUSE.match(colonna):
                    continue
                distinti = con.execute(f'SELECT DISTINCT "{colonna}" FROM "{tabella}" WHERE typeof("{colonna}") = \'text\' '
                                       f'LIMIT {massimo_valori + 1}').fetchall()
                if len(distinti) > massimo_valori:
                    continue                         # troppi valori diversi: è un dato libero, non un'etichetta
                valori += [(tabella, colonna, v) for (v,) in distinti if v and len(v.strip()) >= 2]
    finally:
        con.close()
    trovati = []
    for tabella, colonna, valore in valori:
        esatto = re.compile(rf'(?<![\w-]){re.escape(valore)}(?![\w]|-\d)')
        dove = [d for d, testo in documenti if valore in testo and esatto.search(testo)]
        if dove:
            trovati.append({'tabella': tabella, 'colonna': colonna, 'valore': valore, 'documenti': dove})
    return sorted(trovati, key=lambda x: -len(x['documenti']))


def testo_valori_citati(trovati, per_valore=6):
    if not trovati:
        return '(nessun valore del database compare nei dossier)'
    return '\n'.join(f"{x['tabella']}.{x['colonna']} = '{x['valore']}' — citato in "
                     + ', '.join(x['documenti'][:per_valore]) + (f" e altri {len(x['documenti']) - per_valore}" if len(x['documenti']) > per_valore else '')
                     for x in trovati)


# ------------------------------------------------------------------ agente
ISTRUZIONI_SQL = (
    'Sei l\'analista dati dell\'Investigatore. Scrivi UNA sola query SQLite di lettura (SELECT) che risponde alla domanda '
    'usando lo schema (profilo di ogni tabella, collegamenti dichiarati o dedotti, ipotesi) e le regole della missione. '
    'Per persona, deposito, settore, pratica e gli altri filtri usa i VALORI DEL DATABASE CITATI NEI DOSSIER (esistono '
    'davvero nel database e compaiono nei documenti) e la domanda; gli INDIZI DEI DOSSIER servono a capire quali di quei '
    'valori riguardano la missione. Non inventare valori. Se la DOMANDA indica esplicitamente un valore, la query deve '
    'cercare proprio quello, anche se non compare tra i valori citati: serve a verificare se esiste. Un nome che non è '
    'un valore di nessuna colonna (es. un contenitore che compare solo nei dossier) NON va messo nella query. Se la '
    'domanda nomina un codice, cercalo per codice senza filtri su stato e data e restituisci stato e date. '
    'Se la domanda chiede IL codice o IL permesso (di una persona, per un deposito o una pratica), applica le regole della '
    'missione su stato e date: serve quello valido. Se invece chiede un ELENCO ("quali/tutti i permessi di X", "quali '
    'permessi sono revocati") restituisci TUTTE le righe con codice, pratica, stato, date e motivo, senza filtrare su stato, '
    'date o motivo. '
    'Per esplorare (campioni, conteggi, valori distinti, controlli dei riferimenti) usa COUNT, GROUP BY, DISTINCT e LEFT '
    'JOIN ... WHERE ... IS NULL per i riferimenti mancanti. Collega le tabelle con JOIN sui collegamenti indicati, '
    'confronta i testi esattamente, restituisci le colonne utili a verificare. Non usare ORDER BY ... LIMIT 1 per scegliere. '
    'Rispondi solo con un oggetto JSON nella forma {"sql":"SELECT ..."}.'
)
ISTRUZIONI_PASSO = (
    'Sei l\'analista dati dell\'Investigatore e risolvi la missione un passo alla volta. Per il PASSO indicato scrivi UNA '
    'query SQLite di lettura (SELECT) che restituisce TUTTI i candidati, anche quelli da escludere: le regole sulle date, '
    'sugli stati e sulle revoche le applica il programma, che aggiunge a ogni riga la colonna "esito". Per questo usa '
    'esattamente gli alias di colonna indicati nel passo (es. permesso_inizio, permesso_fine, revoca_effetto, stato). '
    'Usa le ipotesi e i collegamenti dello schema per le JOIN, LEFT JOIN per ciò che può mancare (revoche, sospensioni, '
    'finestre, chiavi). Non filtrare sulle date nella WHERE e non usare LIMIT. Usa i risultati dei passi precedenti '
    '(identificativi) per restringere. Rispondi solo con {"sql":"SELECT ..."}.'
)


def _chiedi_sql(modello, messaggi):
    corpo = {'model': modello, 'stream': False, 'keep_alive': -1, 'think': False, 'format': 'json',
             'options': {'temperature': 0, 'num_ctx': cfg.NUM_CTX, 'num_predict': SQL_TOKEN}, 'messages': messaggi}
    with apri_chat(corpo) as r:
        testo = json.loads(r.read())['message']['content'].strip()
    if testo.startswith('```'):
        testo = '\n'.join(testo.splitlines()[1:-1])
    proposta = json.loads(testo)
    if not isinstance(proposta, dict) or not isinstance(proposta.get('sql'), str):
        raise ValueError(f'formato inatteso: {testo[:200]}')
    return proposta['sql'].strip()


# ------------------------------------------------------------------ scrittura (switch «Scrittura» della chat)
# Il database si modifica solo con lo switch acceso: INSERT, UPDATE, DELETE e REPLACE su tabelle che esistono. Niente DROP, ALTER,
# CREATE, PRAGMA o ATTACH (la struttura resta quella della squadra). Prima di ogni modifica si fa una copia in registro/backup.
RE_SQL_SCRITTURA = re.compile(r'^\s*(insert|update|delete|replace)\b', re.I)
CARTELLA_BACKUP = Path(__file__).resolve().parent.parent / 'registro' / 'backup'
ISTRUZIONI_SCRITTURA = (
    'Sei l\'analista dati dell\'Investigatore e la squadra ti chiede di MODIFICARE il database. Scrivi UNA sola istruzione '
    'SQLite di modifica (INSERT, UPDATE o DELETE) che fa esattamente quello che chiede la richiesta, usando i nomi di tabelle '
    'e colonne dello schema. Restringi UPDATE e DELETE con una WHERE precisa (chiave primaria o valori esatti): non toccare '
    'righe che la richiesta non nomina. I codici sono testo: conserva gli zeri iniziali. Le date nel formato AAAA-MM-GG hh:mm. '
    'Niente DROP, ALTER, CREATE o PRAGMA. Rispondi solo con un oggetto JSON nella forma {"sql":"UPDATE ..."}.'
)


def copia_di_sicurezza(percorso):
    """Copia del database prima della modifica (con l'API di backup di sqlite: coerente anche a database aperto)."""
    CARTELLA_BACKUP.mkdir(parents=True, exist_ok=True)
    p = Path(percorso)
    dest = CARTELLA_BACKUP / f'{p.stem}.{time.strftime("%Y%m%d-%H%M%S")}{p.suffix}'
    n = 1
    while dest.exists():        # due modifiche nello stesso secondo: la seconda copia non deve coprire la prima
        n += 1
        dest = CARTELLA_BACKUP / f'{p.stem}.{time.strftime("%Y%m%d-%H%M%S")}-{n}{p.suffix}'
    sorgente, copia = sqlite3.connect(p), sqlite3.connect(dest)
    try:
        sorgente.backup(copia)
    finally:                # chiuse subito: su Windows un file aperto non si può spostare né cancellare
        sorgente.close()
        copia.close()
    return dest


def _livello_esterno(query):
    """La query senza stringhe, commenti e parentesi: resta solo l'istruzione principale. Serve a cercare la sua WHERE:
    quella di una sottoquery (SET x = (SELECT ... WHERE ...)) o di un commento (-- where) non restringe niente."""
    q = re.sub(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"", "''", query)
    q = re.sub(r'--[^\n]*|/\*.*?(?:\*/|$)', ' ', q, flags=re.S)
    esterno, livello = [], 0
    for c in q:
        livello += (c == '(') - (c == ')')
        if livello == 0 and c != ')':
            esterno.append(c)
    return ''.join(esterno)


def scrittura_vietata(query):
    """Perché l'istruzione non si esegue, o None. UPDATE e DELETE senza WHERE cambierebbero TUTTA la tabella: chi lo vuole
    davvero lo scrive (WHERE 1 = 1)."""
    if not RE_SQL_SCRITTURA.match(query or ''):
        return 'serve una sola istruzione INSERT, UPDATE o DELETE'
    if re.match(r'\s*(update|delete)\b', query, re.I) and not re.search(r'\bwhere\b', _livello_esterno(query), re.I):
        return 'UPDATE e DELETE senza WHERE cambierebbero tutta la tabella: restringi con una WHERE precisa'
    return None


def modifica_database(query, percorso):
    """Esegue UNA istruzione di modifica. Ritorna {righe_modificate} oppure {errore}."""
    if vietata := scrittura_vietata(query):
        return {'errore': vietata}
    if not Path(percorso).is_file():
        return {'errore': 'Carica il database della squadra.'}
    tabelle = set(descrivi(percorso)['tabelle'])
    con = sqlite3.connect(percorso)
    negati = []

    def autorizza(azione, arg1, arg2, db, trigger):
        if azione in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE, sqlite3.SQLITE_READ):
            if arg1 in tabelle:
                return sqlite3.SQLITE_OK
            negati.append(f'la tabella {arg1} non esiste (tabelle: {", ".join(sorted(tabelle))})')
            return sqlite3.SQLITE_DENY
        # TRANSACTION: il BEGIN implicito di Python prima della modifica (il commit è nostro)
        if azione in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_TRANSACTION, sqlite3.SQLITE_SAVEPOINT, getattr(sqlite3, 'SQLITE_RECURSIVE', -1)):
            return sqlite3.SQLITE_OK
        if azione == sqlite3.SQLITE_FUNCTION:
            if (arg2 or '').lower() in FUNZIONI_PERMESSE:
                return sqlite3.SQLITE_OK
            negati.append(f'la funzione {arg2} non è permessa')
            return sqlite3.SQLITE_DENY
        negati.append('sono permesse solo INSERT, UPDATE e DELETE: DROP, ALTER, CREATE, PRAGMA e ATTACH sono vietati')
        return sqlite3.SQLITE_DENY
    con.set_authorizer(autorizza)
    try:
        cursore = con.execute(query)       # execute rifiuta più istruzioni separate da ';'
        con.commit()
        return {'righe_modificate': cursore.rowcount}
    except (sqlite3.Error, sqlite3.Warning) as e:
        con.rollback()
        motivi = list(dict.fromkeys(negati))
        return {'errore': str(e) + (f' ({"; ".join(motivi)})' if motivi else '')}
    finally:
        con.close()


def scrivi_nel_database(modello, richiesta, percorso, m=None):
    """La richiesta di modifica diventa una istruzione SQL (scritta dall'utente, oppure dal modello con fino a TENTATIVI correzioni),
    eseguita dopo la copia di sicurezza. Ritorna {sql, righe_modificate, backup, errore, tentativi, database, colonne, righe}."""
    m = m or missioni.missione(Path(percorso).name)
    base = {'database': Path(percorso).name, 'parametri': m['parametri'], 'titolo': m.get('titolo'), 'scrittura': True,
            'colonne': [], 'righe': [], 'altre_righe': False}
    diretta = RE_SQL_SCRITTURA.match(richiesta)
    messaggi = [{'role': 'system', 'content': f'{ISTRUZIONI_SCRITTURA}\n\nSCHEMA:\n{schema_testo(percorso, m)}'},
                {'role': 'user', 'content': f'RICHIESTA: {richiesta}'}]
    tentativi, backup, esito, query = [], None, {'errore': 'nessuna istruzione proposta'}, ''
    for _ in range(1 if diretta else TENTATIVI):
        try:
            query = richiesta.strip().rstrip(';') if diretta else _chiedi_sql(modello, messaggi)
        except (ValueError, json.JSONDecodeError) as e:
            esito = {'errore': f'il modello non ha prodotto SQL valido ({e})'}
            tentativi.append({'sql': '', 'errore': esito['errore']})
            continue
        if backup is None and not scrittura_vietata(query):      # niente copie per istruzioni che non si eseguono
            backup = copia_di_sicurezza(percorso)
        esito = modifica_database(query, percorso)
        tentativi.append({'sql': query, 'errore': esito.get('errore')})
        if 'errore' not in esito:
            break
        messaggi += [{'role': 'assistant', 'content': json.dumps({'sql': query})},
                     {'role': 'user', 'content': f'Errore: {esito["errore"]}. Correggi l\'istruzione e rispondi solo con il JSON.'}]
    return {**base, 'sql': query, 'righe_modificate': esito.get('righe_modificate'), 'errore': esito.get('errore'),
            'backup': str(backup) if backup else None, 'tentativi': tentativi}


def _una_query(modello, messaggi, percorso, parametri, passo=False, domanda=''):
    """Chiede una query e la esegue, con fino a TENTATIVI correzioni (errore, metodo vietato, 0 righe, elenco in blocco)."""
    tentativi, esito, query = [], {'errore': 'nessuna query proposta'}, ''
    for _ in range(TENTATIVI):
        try:
            query = _chiedi_sql(modello, messaggi)
        except (ValueError, json.JSONDecodeError) as e:
            # l'errore vero va nell'esito: prima restava "nessuna query proposta" e non si capiva che la risposta era troncata
            esito = {'errore': f'il modello non ha prodotto SQL valido ({e})'}
            tentativi.append({'sql': '', 'errore': esito['errore']})
            messaggi.append({'role': 'user', 'content': 'La risposta non era un JSON completo (probabilmente troppo lunga). '
                                                        'Scrivi una query più breve, con solo le tabelle necessarie, e rispondi solo con {"sql":"SELECT ..."}.'})
            continue
        metodo = controlla_query(query, percorso)
        esito = {'errore': metodo} if metodo else interroga_database(query, percorso, MAX_RIGHE_PASSO if passo else MAX_RIGHE)
        tentativi.append({'sql': query, 'errore': esito.get('errore')})
        rifai = None
        if 'errore' in esito:
            rifai = f'Errore: {esito["errore"]}. Correggi la query e rispondi solo con il JSON.'
        elif not passo and troppo_ampia(query, esito):
            esito = {'errore': f'richiesta troppo ampia ({len(esito["righe"])}{"+" if esito.get("altre_righe") else ""} righe '
                               'senza filtri): il tool non consegna elenchi in blocco, servono filtri o un conteggio'}
            tentativi[-1]['errore'] = esito['errore']
            rifai = (f'Troppe righe senza filtri: niente elenchi in blocco. Filtra o aggrega (al massimo {MAX_RISPOSTA} righe) '
                     'e rispondi solo con il JSON.')
        elif not esito.get('righe'):
            assenti = valori_assenti(query, percorso)
            # vale solo per i valori scritti nella domanda: un filtro aggiunto dal modello ('Cocito' per "Archivio Cocito")
            # che non esiste è un errore della query, e si riprova senza
            if assenti and not passo and all(v.lower() in domanda.lower() for v in assenti):
                # «la pratica PX9999»: il valore non esiste, 0 righe È la risposta. Riprovare spingeva il modello a cambiare il filtro
                # finché trovava qualcosa (il codice di un'altra pratica): proprio il codice inventato che la missione vieta.
                esito['nota'] = f'nessuna riga: nel database non esiste {", ".join(repr(v) for v in assenti)}'
            else:
                rifai = ('La query non ha restituito righe'
                         + (f': nel database non esiste {", ".join(repr(v) for v in assenti)}. Usa i valori come sono scritti nel '
                            'database, oppure togli i filtri che la domanda non chiede'
                            if assenti else '. Probabilmente una JOIN sbagliata o un filtro troppo stretto')
                         + '. Controlla valori e collegamenti dello schema e rispondi solo con il JSON.')
        if not rifai:
            break
        messaggi += [{'role': 'assistant', 'content': json.dumps({'sql': query})}, {'role': 'user', 'content': rifai}]
    colonne, righe = annota(esito.get('colonne', []), esito.get('righe', []), parametri, passo) if 'errore' not in esito else ([], [])
    return {'sql': query, 'colonne': colonne, 'righe': righe, 'altre_righe': esito.get('altre_righe', False),
            'errore': esito.get('errore'), 'nota': esito.get('nota'), 'tentativi': tentativi}


def _righe_compatte(p, massimo=30):
    if p.get('errore'):
        return f'(errore: {p["errore"]})'
    righe = [' | '.join('' if v is None else str(v) for v in r) for r in p['righe'][:massimo]]
    return ' | '.join(p['colonne']) + '\n' + '\n'.join(righe) + (f'\n... altre {len(p["righe"]) - massimo} righe' if len(p['righe']) > massimo else '')


# la data è un VALORE da cercare, non il momento della domanda: «valido_fino 2031-10-14 23:59», «scadono il ...», «ts_a > ...»
RE_PRIMA_DI_UN_VALORE = re.compile(r'(\b\w+_\w+|[<>=]|\bfino(?:\s+al?)?|\bscad\w*(?:\s+il)?|\bentro(?:\s+il)?|\bdopo(?:\s+il)?'
                                   r'|\bprima(?:\s+del)?|\btra|\bdal)\s*$', re.I)     # «valido al ...» resta un momento


def data_come_valore(domanda):
    """Vero se la prima data o ora della domanda è un valore da confrontare con le colonne: allora l'istante della missione resta."""
    inizi = [x.start() for r in (missioni.RE_DATA_ISO, missioni.RE_DATA_NUM, missioni.RE_DATA_MESE, missioni.RE_ORA)
             if (x := r.search(domanda))]
    return bool(inizi) and bool(RE_PRIMA_DI_UN_VALORE.search(domanda[:min(inizi)]))


def agente_sql(modello, domanda, indizi, percorso, documenti=(), m=None, avanzamento=None):
    """Interroga il database per la domanda. Tre modi:
    - mappa: "mostrami la mappa delle tabelle" -> il profilo completo, senza query;
    - piano a passi (domande risolutive di una missione con "passi"): una query per passo, esiti calcolati, sintesi;
    - una query (tutto il resto: esplorazione, verifiche, codici).
    avanzamento(n, totale, passo): chiamata dopo ogni passo del piano, per mostrarlo nella pagina mentre il modello scrive il successivo.
    Ritorna {sql, colonne, righe, altre_righe, errore, tentativi, passi?, sintesi?, mappa?, valori_citati, database, parametri}."""
    m = m or missioni.missione(Path(percorso).name)
    # «e alle 12:30?»: la domanda chiede di un altro momento. Vale solo per questa risposta, i parametri salvati non cambiano
    istante = (missioni.istante_in(domanda, m['parametri']['istante'])
               if m.get('parametri', {}).get('istante') and not data_come_valore(domanda) else None)
    if istante and istante != m['parametri']['istante']:
        m = {**m, 'parametri': {**m['parametri'], 'istante': istante}}
    base = {'database': Path(percorso).name, 'parametri': m['parametri'], 'titolo': m.get('titolo')}
    schema = schema_testo(percorso, m)
    if RE_MAPPA.search(domanda):
        return {**base, 'mappa': schema, 'sql': '', 'colonne': [], 'righe': [], 'altre_righe': False, 'errore': None,
                'tentativi': [], 'valori_citati': [], 'relazioni': profila(percorso)['relazioni'],
                'profilo': profila(percorso), 'ipotesi': m.get('ipotesi', {})}
    citati = valori_citati(percorso, documenti) if documenti else []
    if missioni.e_risolutiva(m, domanda):
        passi = []
        for x in m['passi']:
            precedenti = '\n\n'.join(f'PASSO {i + 1} · {p["scopo"]}\nSQL: {p["sql"]}\n{_righe_compatte(p)}' for i, p in enumerate(passi))
            messaggi = [{'role': 'system', 'content': f'{ISTRUZIONI_PASSO}\n\nSCHEMA:\n{schema}'},
                        {'role': 'user', 'content': f'DOMANDA: {domanda}\n\nRISULTATI DEI PASSI PRECEDENTI:\n{precedenti or "(nessuno)"}\n\n'
                                                    f'PASSO {len(passi) + 1} · {missioni.compila(x["scopo"], m["parametri"])}\n'
                                                    f'{missioni.compila(x["obiettivo"], m["parametri"])}'}]
            r = _una_query(modello, messaggi, percorso, m['parametri'], passo=True)
            passi.append({'scopo': missioni.compila(x['scopo'], m['parametri']), **r})
            if avanzamento:
                avanzamento(len(passi), len(m['passi']), passi[-1])
        sintesi = sintesi_varco(passi, m) if (m.get('verdetto') or {}).get('tipo') == 'varco' else ''
        ultimo = passi[-1]
        return {**base, **ultimo, 'errore': next((p['errore'] for p in passi if p['errore']), None), 'passi': passi,
                'sintesi': sintesi, 'tentativi': [t for p in passi for t in p['tentativi']], 'valori_citati': citati[:20]}
    # i documenti che citano DUE o più valori del database li collegano tra loro (es. deposito + pratica): sono gli
    # indizi più forti per capire quale combinazione è quella della missione
    conta = {}
    for x in citati:
        for d in x['documenti']:
            conta[d] = conta.get(d, 0) + 1
    testi = dict(documenti)
    ponte = sorted((d for d, n in conta.items() if n >= 2), key=lambda d: -conta[d])[:8]
    collegamenti = '\n\n'.join(f'[{d}] {testi[d][:700]}' for d in ponte) or '(nessuno)'
    messaggi = [{'role': 'system', 'content': f'{ISTRUZIONI_SQL}\n\nSCHEMA:\n{schema}'},
                {'role': 'user', 'content': f'VALORI DEL DATABASE CITATI NEI DOSSIER:\n{testo_valori_citati(citati)}\n\n'
                                            f'DOCUMENTI CHE COLLEGANO PIÙ VALORI DEL DATABASE:\n{collegamenti}\n\n'
                                            f'INDIZI DEI DOSSIER:\n{indizi or "(nessuno)"}\n\nDOMANDA: {domanda}'}]
    r = _una_query(modello, messaggi, percorso, m['parametri'], domanda=domanda)
    return {**base, **r, 'valori_citati': citati[:20]}


def _punti(d, domanda):
    """Quanto la domanda riguarda il database d: parole della missione, valori nominati, nomi delle tabelle ("r03")."""
    try:
        valori = valori_etichetta(d['percorso'])
        tabelle = descrivi(d['percorso'])['tabelle']
    except Exception:
        valori, tabelle = set(), {}
    q = domanda.lower()
    nominate = sum(2 for n in tabelle if re.search(rf'(?<![\w-]){re.escape(n.lower())}(?![\w-])', q))
    return missioni.punteggio(missioni.missione(d['nome']), domanda, valori) + nominate


def scegli_database(domanda, database):
    """Quale dei database caricati riguarda la domanda: parole chiave della missione, valori e tabelle nominati
    ("Cocito", "V-46", "r08" -> archivio recuperato; "deposito", "LUME" -> accessi). A parità, il più recente."""
    if not database:
        return None
    punti = [(_punti(d, domanda), Path(d['percorso']).stat().st_mtime, i) for i, d in enumerate(database)]
    return database[max(punti)[2]]


def punteggio_database(domanda, database):
    """Il miglior punteggio tra i database (0 = la domanda non nomina nulla di nessuna missione)."""
    return max((_punti(d, domanda) for d in database), default=0)
