"""Funzioni sul testo italiano: stemming, frasi, nomi di persona, luoghi, indizi ed eventi. Nessuno stato, nessun I/O."""
import re
import time
import unicodedata

# ------------------------------------------------------------------ utilità testo
STOP = set('''a ad al alla alle allo ai agli all anche ancora che chi ci col come con contro da dal dalla dalle dallo dai
dagli dall degli dei del della delle dello di dove e ed è era erano fra gli ha hanno ho i il in io la le lei lo loro lui
ma mi ne nei nel nella nelle nello noi non o per però più poi qua quale quali quando questa queste questi questo qui
se sei si sia siamo siete sono su sua sue sui sul sulla sulle suo suoi ti tra tu tutti tutto un una uno vi voi cosa
cui perché quel quella quelle quelli quello dunque quindi oppure essere stato stata stati state fa fatto molto meno
c l d s n un\''''.split())

# parole che, scritte con la maiuscola, NON sono nomi di persona
NON_PERSONA = set('''id dio stato comune paese città centrale protocollo lazzaro italia europa governo esercito polizia
croce rossa regione provincia ministero radio messaggio fonte fonti documento documenti giorno ora oggi ieri domani
nord sud est ovest zona settore quartiere rifugio ospedale stazione secondo inoltre tuttavia infatti però nessun nessuna
sì no risposta domanda modalità demo investigatore uno due tre quattro cinque sei sette otto nove dieci venti cento mille
primo prima secondo seconda terzo terza ultimo ultima alcuni alcune molti molte nessuno ogni questo questa'''.split())

PREFISSI_LUOGO = set('''via viale piazza piazzale corso largo vicolo ospedale stazione ponte porta chiesa scuola parco
quartiere zona rifugio settore torre monte fiume lago hotel albergo bar banca caserma palazzo villa cascina borgo
strada località centro istituto università clinica farmacia'''.split())

TITOLI = set('''signor signore signora signorina dottor dottore dottoressa dott dr professor professore professoressa
prof capitano tenente sergente caporale agente ispettore ispettrice commissario sindaco don suor padre ingegner
ingegnere avvocato avv infermiere infermiera maresciallo colonnello generale'''.split())

PAROLA_MAIUSC = r"[A-ZÀ-ÖØ-Ý](?:[’'][A-ZÀ-ÖØ-Ý])?[a-zà-öø-ÿ]+"
RE_NOME = re.compile(rf"(?<![\w’'])({PAROLA_MAIUSC}(?:[ \t]+{PAROLA_MAIUSC}){{0,2}})(?![\w’'])")
RE_FRASI = re.compile(r'(?<=[.!?…])\s+|\n+')


def senza_accenti(t):
    return ''.join(c for c in unicodedata.normalize('NFD', t) if unicodedata.category(c) != 'Mn')


def radice(parola):
    """Stemming molto leggero per l'italiano: basta per BM25 e non costa nulla."""
    p = senza_accenti(parola.lower())
    for fine in ('zioni', 'zione', 'mente', 'menti', 'ando', 'endo', 'ato', 'ata', 'ati', 'ate', 'ito', 'ita', 'iti', 'ite'):
        if len(p) > len(fine) + 3 and p.endswith(fine):
            return p[:-len(fine)]
    if len(p) > 4 and p[-1] in 'aeio':
        return p[:-1]
    return p


def parole(testo):
    return [radice(w) for w in re.findall(r"[\wÀ-ÿ]+", testo.lower()) if w not in STOP and len(w) > 1]


def frasi(testo):
    return [f.strip() for f in RE_FRASI.split(testo) if f.strip()]


def estrai_nomi(testo):
    """Riconoscimento euristico dei nomi di persona: sequenze di parole maiuscole fuori da inizio frase,
    oppure precedute da un titolo (dottor, agente, ...). Ritorna l'elenco dei nomi trovati, in ordine."""
    trovati = []
    for frase in frasi(testo):
        for m in RE_NOME.finditer(frase):
            prima = frase[:m.start()]
            coda = prima.rstrip()
            # dopo «, virgolette o due punti inizia un discorso diretto: conta come inizio frase
            inizio_frase = coda.strip(' \t«»"“”‘’\'()-–—:') == '' or coda.endswith(('«', '"', '“', ':', '—', '–', '(', ')', ']'))
            ultima = re.findall(r"[\wÀ-ÿ]+", prima.lower())
            ultima = ultima[-1] if ultima else ''
            token = m.group(1).split()
            titolato = ultima in TITOLI
            while token and token[0].lower() in TITOLI:
                token.pop(0)
                titolato = True
            if inizio_frase and token and not titolato:
                if len(token) == 1:
                    continue
                if token[0].lower() in STOP or token[0].lower() in NON_PERSONA:
                    token.pop(0)
                    if len(token) == 1 and not re.search(r'\w', prima):
                        # "La Serra" a inizio frase: troppo ambiguo
                        continue
            while token and token[-1].lower() in STOP:
                token.pop()
            if not token:
                continue
            if token[0].lower() in PREFISSI_LUOGO or any(t.lower() in NON_PERSONA for t in token):
                continue
            if len(token) == 1 and not titolato and ultima in ('in', 'verso', 'presso', 'nel', 'nella', 'a'):
                continue
            trovati.append(' '.join(token))
    return trovati


# ------------------------------------------------------- luoghi, indizi, eventi
_PAROLE_LUOGO = '|'.join(sorted(PREFISSI_LUOGO, key=len, reverse=True))
# "Piazza Duomo", "rifugio nord", "settore 4", "Ospedale San Carlo"
RE_LUOGO = re.compile(rf"(?<![\w’'])((?i:{_PAROLE_LUOGO})(?:[ \t]+(?:{PAROLA_MAIUSC}|nord|sud|est|ovest|centrale|\d+[a-z]?)){{1,3}})(?![\w’'])")
# "a Torino", "verso Monza", "presso Villa Reale"
RE_LUOGO_PREP = re.compile(rf"(?<![\w’'])(?:a|in|presso|verso)[ \t]+({PAROLA_MAIUSC}(?:[ \t]+{PAROLA_MAIUSC})?)(?![\w’'])")

RE_INDIZIO = re.compile(r'registr|messagg|trasmission|pausa|frase|tracc|impront|segnal|prov[ae]\b|indizi|foto|chiav|sangue|'
                        r'bigliett|codice|ripet|stess[aoie]\b|sospett|manca|cancellat|interrott|originali|dettagli', re.I)
RE_EVENTO = re.compile(r'quarantena|evacua|attacc|esplos|incendi|fuga|fugg|scompar|arrivat|partit|chiamat|interromp|ordina|'
                       r'chius|croll|contagi|inseguon|\b(?:è|sono|ha|hanno)\s+\w+(?:ato|ata|ati|ate|uto|uta|ito|ita)\b', re.I)


def chiave(testo):
    return re.sub(r'\s+', ' ', senza_accenti(testo.lower())).strip(' .,;:!?«»"')


def estrai_luoghi(testo, persone_note=()):
    trovati = {}
    for m in RE_LUOGO.finditer(testo):
        nome = m.group(1)
        if any(p.endswith(nome.split(None, 1)[-1]) for p in persone_note):
            continue
        trovati.setdefault(chiave(nome), nome[0].upper() + nome[1:])
    for m in RE_LUOGO_PREP.finditer(testo):
        nome = m.group(1)
        primo = nome.split()[0].lower()
        if primo in PREFISSI_LUOGO or primo in STOP or primo in NON_PERSONA or primo in TITOLI or nome in persone_note:
            continue
        trovati.setdefault(chiave(nome), nome)
    return [{'nome': n} for n in trovati.values()]


def titolo_breve(frase, parole_max=7):
    p = frase.strip(' «»"').split()
    return ' '.join(p[:parole_max]).rstrip('.,;:') + ('…' if len(p) > parole_max else '')


def estrai_con_regole(fonti):
    """Indizi ed eventi senza modello: frasi delle fonti con parole tipiche. Serve in demo e come riserva."""
    indizi, eventi = [], []
    for f in fonti:
        for frase in frasi(f['testo']):
            if frase.upper().startswith('ID ') or len(frase.split()) < 5:
                continue
            ni, ne = len(RE_INDIZIO.findall(frase)), len(RE_EVENTO.findall(frase))
            if not ni and not ne:
                continue
            voce = {'titolo': titolo_breve(frase), 'doc': f['doc'], 'citazione': frase, 'verificato': True}
            (indizi if ni >= ne else eventi).append((max(ni, ne), voce))
    scegli = lambda elenco: [v for _, v in sorted(elenco, key=lambda x: -x[0])[:3]]
    return scegli(indizi), scegli(eventi)


def ms(t):
    """Millisecondi trascorsi da t (time.perf_counter())."""
    return round((time.perf_counter() - t) * 1000, 1)
