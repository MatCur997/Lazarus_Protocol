"""Funzioni sul testo italiano: stemming, frasi, nomi di persona, luoghi, indizi ed eventi. Nessuno stato, nessun I/O."""
import re
import time
import unicodedata
from scenario import PREFISSI_LUOGO, PREFISSI_COSA, PREFISSI_SANTI, NOMI_PROPRI, RUOLI_PERSONA, VERBI_PERSONA

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
primo prima secondo seconda terzo terza ultimo ultima alcuni alcune molti molte nessuno ogni questo questa
ipotesi indizio indizi dubbi dubbio evidenze evidenza fatto fatti nota note
storia biografia collegamenti descrizione caratteristiche personaggi bibliografia trama geografia etimologia
filmografia discografia opere carriera onorificenze riconoscimenti curiosità origini morfologia sistematica
tassonomia habitat distribuzione popolazione economia cultura voci correlate esterni altri anni
mostrate caricate usate collegate riformulate verificate provate conservate annotate consegnate scoprite
accettiamo potete parla cosa dove'''.split())
# riga 6: imperativi delle istruzioni di missione ("Mostrate i file recuperati"), maiuscoli a inizio cella
# riga 2: etichette delle risposte; righe 3-5: titoli di sezione delle voci enciclopediche ("Biografia Nato a ...")


NON_PERSONA_INIZIO = PREFISSI_LUOGO | PREFISSI_COSA

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
        fine_cosa = None      # dove finisce l'ultimo nome di luogo/cosa: "ripetitori Rame e Fosso" -> anche Fosso
        for m in RE_NOME.finditer(frase):
            prima = frase[:m.start()]
            parola_prima = re.findall(r"[\wÀ-ÿ]+", prima)
            # "il deposito Darsena Vuota", "ai ripetitori Rame": il nome è di quella cosa, non di una persona
            if (parola_prima and parola_prima[-1].islower() and parola_prima[-1] in NON_PERSONA_INIZIO) \
                    or (fine_cosa is not None and frase[fine_cosa:m.start()].strip() in ('e', 'ed', 'o', ',')):
                fine_cosa = m.end()
                continue
            coda = prima.rstrip()
            # dopo «, virgolette o due punti inizia un discorso diretto: conta come inizio frase
            # anche dopo un grassetto Markdown (**Risposta:** ...) o un punto elenco
            inizio_frase = coda.strip(' \t«»"“”‘’\'()-–—:*') == '' or coda.endswith(('«', '"', '“', ':', '—', '–', '(', ')', ']', '*'))
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
            if token[0].lower() in NON_PERSONA_INIZIO or any(t.lower() in NON_PERSONA for t in token):
                continue
            if len(token) == 1 and not titolato and ultima in ('in', 'verso', 'presso', 'nel', 'nella', 'a'):
                continue
            trovati.append(' '.join(token))
    return trovati


# "Riva, 34 anni", "Sara Riva — tesserino T447", "Serra (nato a ...)"
RE_ANAGRAFICA = re.compile(r"^\s*[,(—–-]\s*(?:\d{1,3}\s+anni|nat[oa]\b|tesserino|matricola|badge)", re.I)
RE_AUSILIARE = re.compile(r"^\s*,?\s*(?:ha|aveva|avrebbe|abbia|è|era|fu|sarebbe|viene|venne)?\s*([\wÀ-ÿ]+)")


def prova_persona(frase, nome):
    """Il nome, in questa frase, è di sicuro una persona? Nome di battesimo noto, titolo o ruolo davanti
    ("il corriere Spina"), verbo da persona dopo ("Serra ha chiesto") o dati anagrafici ("Riva, 34 anni")."""
    if senza_accenti(nome.split()[0].lower()) in NOMI_PROPRI:
        return True
    for m in re.finditer(rf'(?<![\w’]){re.escape(nome)}(?![\w’])', frase):
        prima = re.findall(r"[\wÀ-ÿ]+", frase[:m.start()].lower())
        if prima and (prima[-1] in TITOLI or prima[-1] in RUOLI_PERSONA):
            return True
        dopo = frase[m.end():]
        verbo = RE_AUSILIARE.match(dopo)
        if (verbo and verbo.group(1).lower() in VERBI_PERSONA) or RE_ANAGRAFICA.match(dopo):
            return True
    return False


# ------------------------------------------------------- luoghi, indizi, eventi
_PAROLE_LUOGO = '|'.join(sorted(PREFISSI_LUOGO, key=len, reverse=True))
# "Piazza Duomo", "rifugio nord", "settore 4", "Ospedale San Carlo"
RE_LUOGO = re.compile(rf"(?<![\w’'])((?i:{_PAROLE_LUOGO})(?:[ \t]+(?:{PAROLA_MAIUSC}|nord|sud|est|ovest|centrale|\d+[a-z]?)){{1,3}})(?![\w’'])")
# "Sant'Anna", "Sant’Ambrogio"
RE_LUOGO_SANT = re.compile(r"(?<![\w’'])(Sant['’][A-ZÀ-ÖØ-Ý][a-zà-öø-ÿ]+)(?![\w’'])")
# "a Torino", "verso Monza", "presso Villa Reale"
RE_LUOGO_PREP = re.compile(rf"(?<![\w’'])(?:a|in|presso|verso)[ \t]+({PAROLA_MAIUSC}(?:[ \t]+{PAROLA_MAIUSC})?)(?![\w’'])")


def chiave(testo):
    return re.sub(r'\s+', ' ', senza_accenti(testo.lower())).strip(' .,;:!?«»"')


def chiave_id(testo):
    """ID di documento confrontabili: il modello scrive spesso [LX4-7783] per il file LX4_7783."""
    return re.sub(r'[\W_]+', '', testo.lower())


def estrai_luoghi(testo, persone_note=()):
    trovati = {}
    for m in RE_LUOGO.finditer(testo):
        nome = m.group(1)
        parti = nome.split()
        # "laboratorio Santa Irene" -> "Santa Irene": il nome proprio è quello che compare anche nelle risposte
        if len(parti) > 2 and parti[0].lower() not in PREFISSI_SANTI and parti[1].lower() in PREFISSI_SANTI:
            nome = ' '.join(parti[1:])
        if any(p.endswith(nome.split(None, 1)[-1]) for p in persone_note):
            continue
        trovati.setdefault(chiave(nome), nome[0].upper() + nome[1:])
    for m in RE_LUOGO_SANT.finditer(testo):
        trovati.setdefault(chiave(m.group(1)), m.group(1))
    for m in RE_LUOGO_PREP.finditer(testo):
        nome = m.group(1)
        primo = nome.split()[0].lower()
        if primo in PREFISSI_LUOGO or primo in STOP or primo in NON_PERSONA or primo in TITOLI or nome in persone_note:
            continue
        trovati.setdefault(chiave(nome), nome)
    return [{'nome': n} for n in trovati.values()]



# "**Eventi:**", "**Indizi:**", "**Ipotesi:**" ... e voci "- **Nome breve**: dettaglio [G1-03]"
RE_SEZIONE = re.compile(r'^\s*(?:[-*•]\s*)?\*\*\s*(risposta|eventi|evento|indizi|indizio|ipotesi|dubbi|evidenze)\s*:?\s*\*\*\s*:?\s*', re.I)
RE_VOCE = re.compile(r'^\s*(?:[-*•]\s+)?\*\*([^*]{2,60}?)\s*:?\*\*\s*[:—–-]?\s*(.+)$')
RE_ID = re.compile(r'\[([^\]]+)\]')
SEZIONI_ELEMENTI = {'eventi': 'evento', 'evento': 'evento', 'indizi': 'indizio', 'indizio': 'indizio', 'ipotesi': 'ipotesi'}
SOGLIA_RISCONTRO = {'evento': 0.35, 'indizio': 0.35, 'ipotesi': 0.2}   # parte delle parole della voce presenti nel documento citato
NOMI_GENERICI = {'evento', 'eventi', 'indizio', 'indizi', 'ipotesi'}


def voci_della_risposta(risposta):
    """Eventi, indizi e ipotesi che il modello ha scritto con un nome: [{genere, nome, testo}]."""
    sezione, voci = None, []
    for riga in risposta.splitlines():
        m = RE_SEZIONE.match(riga)
        if m:
            sezione = SEZIONI_ELEMENTI.get(m.group(1).lower())
            riga = riga[m.end():]
        v = RE_VOCE.match(riga) if sezione else None
        if v and chiave(v.group(1)) not in NOMI_GENERICI:
            voci.append({'genere': sezione, 'nome': v.group(1).strip(' :'), 'testo': v.group(2).strip()})
    return voci


def _radici(testo):
    """Prime 5 lettere delle radici: "registrati" e "registrazione" contano come la stessa parola."""
    return {p[:5] for p in parole(testo)}


def _somiglianza(cercate, testo):
    comuni = len(cercate & _radici(testo))
    return comuni / max(1, len(cercate)) if comuni >= 2 else 0.0


def riscontro(testo, fonti, soglia, nome=''):
    """La frase dei documenti citati ([ID]) che conferma la voce, oppure None se non c'è un riscontro vero.
    Il confronto si fa con tutto il documento citato (il modello riformula e unisce più frasi);
    come citazione si mostra la frase con più parole in comune."""
    ids = {chiave_id(x) for m in RE_ID.findall(testo) for x in re.split(r'[,;]', m)}
    cercate = _radici(f'{nome} {RE_ID.sub("", testo)}')
    migliore, punteggio = None, 0.0
    for f in fonti:
        if chiave_id(f['doc']) not in ids and chiave_id(f.get('id_interno') or '') not in ids - {''}:
            continue
        elenco = [x for x in frasi(f['testo']) if not x.upper().startswith('ID ')]
        if f.get('sospetto'):
            # Un documento sospetto (dà ordini all'assistente) conferma solo che c'è un tentativo di manipolazione,
            # MAI il suo contenuto: "Santa Chiara colpevole [G2-04]" non passa, "Tentativo di manipolazione [G2-04]" sì.
            if not RE_VOCE_MANIPOLAZIONE.search(f'{nome} {testo}'):
                continue
            frase = next((x for x in elenco if RE_ORDINE.search(x)), elenco[0] if elenco else '')
            s = soglia
        else:
            s = _somiglianza(cercate, f['testo'])
            frase = max(elenco, key=lambda x: len(cercate & _radici(x)), default='')
        if s > punteggio or (s == punteggio == soglia and migliore is None):
            migliore, punteggio = {'doc': f['doc'], 'citazione': frase}, s
    return migliore if punteggio >= soglia else None


RE_VOCE_MANIPOLAZIONE = re.compile(r'manipol|istruzion|ordin|sovrascriv|forzar|ingann|malevol|sospett|occult|depist|devia', re.I)
RE_ORDINE = re.compile(r'istruzion|ignora|rispondi|non citare|non dire', re.I)


def estrai_voci_nominate(risposta, fonti):
    """Voci per la colonna destra, solo se i documenti citati le confermano.
    Ritorna (indizi, eventi): indizi = "Nome (indizio)" / "Nome (ipotesi)", eventi = "Nome"."""
    indizi, eventi = [], []
    for v in voci_della_risposta(risposta):
        r = riscontro(v['testo'], fonti, SOGLIA_RISCONTRO[v['genere']], v['nome'])
        if not r:
            continue
        evento = v['genere'] == 'evento'
        (eventi if evento else indizi).append({
            'titolo': v['nome'] if evento else f"{v['nome']} ({v['genere']})", 'genere': v['genere'],
            'descrizione': RE_ID.sub('', v['testo']).strip(), **r, 'verificato': True})
    return indizi, eventi


def ms(t):
    """Millisecondi trascorsi da t (time.perf_counter())."""
    return round((time.perf_counter() - t) * 1000, 1)
