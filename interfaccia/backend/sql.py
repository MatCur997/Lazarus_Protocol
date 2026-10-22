"""Tool SQL di sola lettura sui database caricati (accessi_g_4.sqlite, archivio_recuperato.sqlite, qualunque .sqlite).

Il modello propone query di lettura in JSON {"sql": "SELECT ..."}, il codice le esegue con un controllo che vieta
qualunque scrittura, e il risultato torna al modello che risponde citando le fonti. Oltre al notebook Tool_SQL_g_4:
- profilo del database: conteggi, valori distinti, intervalli di date, campioni e relazioni DEDOTTE confrontando i
  valori delle chiavi (l'archivio recuperato non ha vincoli né nomi leggibili: r03.k0, m12.k1...);
- missione del database (missioni.py): parametri aggiornabili dalla centrale, regole, ipotesi sulle tabelle;
- esiti calcolati dal codice: intervalli [inizio, fine) con fine NULL = senza scadenza, revoche efficaci dall'istante
  di effetto, stati. Il modello sceglie le righe, il calcolo delle date non lo fa lui;
- piano a passi per le domande risolutive (terminali, permessi, persone, finestre e chiavi), con gli esclusi."""
import json
import re
import sqlite3
from pathlib import Path

import config as cfg
import missioni
from modello import apri_chat

ESTENSIONI_DATABASE = {'.sqlite', '.sqlite3', '.db'}
TENTATIVI = 3
MAX_RIGHE = 50
MAX_RIGHE_PASSO = 300   # un passo del piano deve vedere TUTTI i candidati (persone delle squadre: ~100 righe)
MAX_RISPOSTA = 10        # righe di un elenco SENZA filtri: oltre è un'estrazione in blocco, non un'indagine
FUNZIONI_PERMESSE = {'count', 'min', 'max', 'lower', 'upper', 'length', 'coalesce', 'sum', 'avg', 'trim', 'substr',
                     'replace', 'date', 'datetime', 'ifnull', 'abs', 'round', 'instr', 'like', 'group_concat',
                     'strftime', 'julianday', 'iif', 'nullif', 'typeof', 'total'}
# domande che chiedono di cambiare i DATI: il database è in sola lettura, si risponde senza proporre SQL.
# Serve un oggetto (database, tabella, permesso...): "la centrale ha aggiornato i parametri" non è una scrittura.
RE_SCRITTURA = re.compile(
    r'\b(drop|delete|update|insert|alter|truncate)\b\s+(table|from|into|\w+\s+set)'
    r'|\b(cancell|elimin|rimuov|modific|aggiorn|inserisc|aggiung|svuot|sovrascriv|altera|forza)\w*\b.{0,40}?'
    r'\b(database|db|tabell\w*|dat[io]\b|rig[ah]e?\b|record|permess\w*|codic\w*|valor\w*|accessi|autorizzazion\w*|'
    r'abilitazion\w*|revoc\w*|sospension\w*|stato|chiav\w*)', re.I)
RE_ORDINE_ARBITRARIO = re.compile(r'\border\s+by\b[^;]*\blimit\s+1\b', re.I | re.S)
RE_MAPPA = re.compile(r'\b(mappa|dizionario|struttura|schema)\b.{0,30}\b(tabell\w*|database|dati|archivio)\b'
                      r'|\b(tabell\w*)\b.{0,20}\b(significat\w*|relazion\w*|collegament\w*)\b', re.I)


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


def descrivi(percorso):
    """{tabelle: {nome: [colonne]}, collegamenti: [...], righe: {nome: n}} letti dal database."""
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
    _CACHE_PROFILO.clear()
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
def interroga_database(query, percorso, massimo=MAX_RIGHE):
    """Esegue UNA query di sola lettura. Ritorna {colonne, righe, altre_righe} oppure {errore}."""
    if not isinstance(query, str) or not query.strip():
        return {'errore': 'Scrivi una query SQL.'}
    if not Path(percorso).is_file():
        return {'errore': 'Carica il database della squadra.'}
    con = _connetti(percorso)
    tabelle = set(descrivi(percorso)['tabelle'])

    def autorizza(azione, arg1, arg2, db, trigger):
        if azione == sqlite3.SQLITE_SELECT:
            return sqlite3.SQLITE_OK
        if azione == sqlite3.SQLITE_READ:
            return sqlite3.SQLITE_OK if arg1 in tabelle else sqlite3.SQLITE_DENY
        if azione == sqlite3.SQLITE_FUNCTION:
            return sqlite3.SQLITE_OK if (arg2 or '').lower() in FUNZIONI_PERMESSE else sqlite3.SQLITE_DENY
        if azione == getattr(sqlite3, 'SQLITE_RECURSIVE', -1):      # WITH ... (tabelle temporanee della query)
            return sqlite3.SQLITE_OK
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
        righe = cursore.fetchmany(massimo + 1)
        return {'colonne': [x[0] for x in cursore.description], 'righe': [list(r) for r in righe[:massimo]],
                'altre_righe': len(righe) > massimo}
    except (sqlite3.Error, sqlite3.Warning) as e:
        return {'errore': str(e)}
    finally:
        con.close()


RE_ALIAS = re.compile(r'\b(?:from|join)\s+"?(\w+)"?(?:\s+(?:as\s+)?(?!on\b|where\b|join\b|left\b|inner\b|group\b|order\b|limit\b)(\w+))?', re.I)
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

    for a1, c1, a2, c2 in RE_UGUALE.findall(query or ''):
        b1, b2 = bersaglio(a1, c1), bersaglio(a2, c2)
        if b1 and b2 and b1 != b2:
            t1, t2 = alias[a1], alias[a2]
            return (f'JOIN incoerente: {t1}.{c1} si riferisce a {b1}, {t2}.{c2} si riferisce a {b2}. Collega una chiave '
                    f'esterna con la chiave primaria (k0) della tabella a cui punta, secondo i collegamenti dello schema.')
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


def _attivo(inizio, fine, t):
    return inizio is not None and str(inizio) <= t and (fine is None or t < str(fine))


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
            if v.get(e) is not None and str(v[e]) <= t:
                motivi.append(f'{e[:-8]} efficace dal {v[e]}')
        for s in stati:
            if v.get(s) is not None and str(v[s]).lower() not in STATI_VALIDI:
                motivi.append(f'{s} {v[s]}')
        if canale and 'canale' in v and v['canale'] is not None and str(v['canale']).lower() != canale.lower():
            motivi.append(f'canale {v["canale"]} diverso da {canale}')
        nuove.append(list(r) + ['VALIDO' if not motivi else 'ESCLUSO: ' + '; '.join(motivi)])
    return list(colonne) + ['esito'], nuove


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
    # passo 2: squadre con un permesso valido per ogni ruolo
    per_squadra = {}
    for r in righe(1):
        if valido(r):
            per_squadra.setdefault((r.get('squadra'), r.get('nome_squadra')), set()).add(str(r.get('ruolo')).upper())
    squadre = [s for s, rr in per_squadra.items() if all(x in rr for x in ruoli)]
    # la squadra nel passo 3 può arrivare come codice o come nome: si riporta sempre al codice del passo 2
    codice_di = {str(n): s for s, n in per_squadra} | {str(s): s for s, _ in per_squadra}
    for r in righe(1):
        codice_di.setdefault(str(r.get('nome_squadra')), r.get('squadra'))
        codice_di.setdefault(str(r.get('squadra')), r.get('squadra'))
    # passo 3: persone valide per ruolo dentro quelle squadre
    persone = {}
    esclusi = []
    for r in righe(2):
        squadra = codice_di.get(str(r.get('squadra')), codice_di.get(str(r.get('nome_squadra')), r.get('squadra')))
        chiave = (squadra, str(r.get('ruolo')).upper())
        if valido(r):
            persone.setdefault(chiave, {})[r.get('persona')] = f'{r.get("nome")} ({r.get("badge")})'
        else:
            esclusi.append(f'{r.get("nome")} ({r.get("badge")}, {r.get("ruolo")}): {str(r.get("esito"))[9:]}')
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
    if esclusi:
        righe_testo.append('Persone escluse (esempi): ' + ' | '.join(esclusi[:6]))
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
             'options': {'temperature': 0, 'num_ctx': cfg.NUM_CTX, 'num_predict': 600}, 'messages': messaggi}
    with apri_chat(corpo) as r:
        testo = json.loads(r.read())['message']['content'].strip()
    if testo.startswith('```'):
        testo = '\n'.join(testo.splitlines()[1:-1])
    proposta = json.loads(testo)
    if not isinstance(proposta, dict) or not isinstance(proposta.get('sql'), str):
        raise ValueError(f'formato inatteso: {testo[:200]}')
    return proposta['sql'].strip()


def _una_query(modello, messaggi, percorso, parametri, passo=False):
    """Chiede una query e la esegue, con fino a TENTATIVI correzioni (errore, metodo vietato, 0 righe, elenco in blocco)."""
    tentativi, esito, query = [], {'errore': 'nessuna query proposta'}, ''
    for _ in range(TENTATIVI):
        try:
            query = _chiedi_sql(modello, messaggi)
        except (ValueError, json.JSONDecodeError) as e:
            tentativi.append({'sql': '', 'errore': f'il modello non ha prodotto SQL valido ({e})'})
            messaggi.append({'role': 'user', 'content': 'Rispondi solo con {"sql":"SELECT ..."}.'})
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
            rifai = ('La query non ha restituito righe. Probabilmente un filtro usa un valore che nel database non esiste '
                     'o una JOIN sbagliata. Controlla valori e collegamenti dello schema e rispondi solo con il JSON.')
        if not rifai:
            break
        messaggi += [{'role': 'assistant', 'content': json.dumps({'sql': query})}, {'role': 'user', 'content': rifai}]
    colonne, righe = annota(esito.get('colonne', []), esito.get('righe', []), parametri, passo) if 'errore' not in esito else ([], [])
    return {'sql': query, 'colonne': colonne, 'righe': righe, 'altre_righe': esito.get('altre_righe', False),
            'errore': esito.get('errore'), 'tentativi': tentativi}


def _righe_compatte(p, massimo=30):
    if p.get('errore'):
        return f'(errore: {p["errore"]})'
    righe = [' | '.join('' if v is None else str(v) for v in r) for r in p['righe'][:massimo]]
    return ' | '.join(p['colonne']) + '\n' + '\n'.join(righe) + (f'\n... altre {len(p["righe"]) - massimo} righe' if len(p['righe']) > massimo else '')


def agente_sql(modello, domanda, indizi, percorso, documenti=(), m=None):
    """Interroga il database per la domanda. Tre modi:
    - mappa: "mostrami la mappa delle tabelle" -> il profilo completo, senza query;
    - piano a passi (domande risolutive di una missione con "passi"): una query per passo, esiti calcolati, sintesi;
    - una query (tutto il resto: esplorazione, verifiche, codici).
    Ritorna {sql, colonne, righe, altre_righe, errore, tentativi, passi?, sintesi?, mappa?, valori_citati, database, parametri}."""
    m = m or missioni.missione(Path(percorso).name)
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
    r = _una_query(modello, messaggi, percorso, m['parametri'])
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
