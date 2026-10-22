"""Tutto ciò che dipende dallo scenario in un solo posto: formato dei documenti e dei codici, giorni, parole generiche davanti ai nomi, nomi italiani.
Il resto del backend resta generico: per un'indagine diversa si cambia questo file, non la logica di ricerca.

Il testo del prompt che descrive il caso (nome, squadra, ambientazione) sta in `app_lazarus/lazarus.py`, nel blocco SCENARIO in cima: è lì perché lazarus.py
funziona anche da solo come chat nel terminale. Il profilo del caso per la pertinenza (`PROFILO_DEL_CASO`) si ricava da quel blocco.
Resta fuori da qui ciò che è dato, non codice: le persone escluse a mano (`interfaccia/registro/non_persone.txt`) e i metadati corretti a mano."""

import re

# ---- Il giorno dell'indagine di un documento (da indice.py)
RE_GIORNO_CARTELLA = re.compile(r'giorn[oi]?[ _-]*0*(\d{1,2})(?!\d)', re.I)        # Dati_giorno_2, Imprevisti_giorno_2

RE_GIORNO_TESTO = re.compile(r'giorno\s+operativo\s+0*(\d{1,2})(?!\d)', re.I)      # "giorno operativo 02"

RE_GIORNO_ID = re.compile(r'^G(\d{1,2})-\d', re.I)                                 # G2-01

# ---- Formato di codici e ID dei documenti (per la ricerca a salti) (da motore.py)
# Ricerca a salti: pratica -> lotto -> armadio sono in documenti diversi e la domanda nomina solo il primo anello.
# Dai documenti migliori si prendono i codici (M462, K491...) e si cercano come parola intera: "M462" non è "M462-050".
RE_CODICE = re.compile(r'(?<![\w-])([A-Z]{1,4}-?\d{2,5})(?![\w]|-\d)')

RE_ID_DOCUMENTO = re.compile(r'^(?:LX\d?|G\d|ARCH)-?\d+$')

# ---- Parole generiche davanti ai nomi di luoghi e di cose (non sono persone) (da testo.py)
PREFISSI_LUOGO = set('''via viale piazza piazzale corso largo vicolo ospedale stazione ponte porta chiesa scuola parco
quartiere zona rifugio settore torre monte fiume lago hotel albergo bar banca caserma palazzo villa cascina borgo
strada località centro istituto università clinica farmacia laboratorio deposito magazzino ufficio archivio base
porto aeroporto campo carcere cimitero mercato fabbrica capannone bunker struttura complesso padiglione reparto
ripetitore ripetitori antenna antenne traliccio cabina centralina impianto stabilimento postazione presidio sede edificio
varco cancello checkpoint galleria tunnel diga pozzo serbatoio silo hangar molo banchina piattaforma nodo snodo
svincolo ingresso uscita blocco ala sottostazione'''.split())
# "Santa Chiara", "San Carlo", "Sant'Anna": nei documenti sono luoghi (laboratori, ospedali, quartieri), non persone
PREFISSI_SANTI = {'san', 'santa', 'santo', 'santi', 'sant'}
PREFISSI_LUOGO |= PREFISSI_SANTI

# apparecchi, mezzi, gruppi e documenti: "Generatore Nord", "Squadra Alfa", "Veicolo 3" non sono persone (e nemmeno luoghi).
# Come PREFISSI_LUOGO valgono solo come PRIMA parola, così un cognome come "Canale" o "Piano" resta una persona.
PREFISSI_COSA = set('''generatore sensore sensori terminale dispositivo apparato apparecchio trasmettitore ricevitore
segnale rete frequenza server computer modulo unità veicolo mezzo camion furgone ambulanza elicottero drone convoglio
treno codice scheda registro rapporto verbale bollettino comunicato allarme allerta operazione missione progetto
programma squadra gruppo team nucleo pattuglia turno orologio display allegato campione provetta ceppo focolaio'''.split())

# ---- Nomi italiani: maschili in -a e femminili che non finiscono in -a (la foto segue il sesso) (da volti.py)
# Nomi italiani in -a che sono maschili, e nomi in -e/-i/consonante che sono femminili.
MASCHILI_IN_A = set('''luca andrea nicola mattia elia enea battista gianluca giosuè joshua tobia isaia geremia
zaccaria ezechiele barnaba noah sasha nikita akira'''.split())

FEMMINILI = set('''irene beatrice alice adele agnese rachele matilde clarice clotilde cloe chloe ester esther
miriam ingrid carmen dolores mercedes ruth judith giuditta noemi naomi suzanne marion rosmary rosemary
kim jennifer ellen helen eleonore ines inès abigail iris nives vivien lilian gwen daphne zoe zoé'''.split())
