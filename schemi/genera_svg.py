"""Rigenera flusso_algoritmo.svg: il diagramma di flusso di una risposta di Lazzaro (a sinistra) e le funzioni del chatbot con le giornate che risolvono (a destra).
Si lancia con: python schemi/genera_svg.py   (solo libreria standard). Se cambia il percorso di una domanda, si cambia qui e si rilancia.
Le giornate sono indicate solo dove sono certe: il commento in app_lazarus/lazarus.py («Giorno 1: tutti i testi. Dal giorno 3 … recupero RAG»), i nomi e il contenuto
delle cartelle dei dati (dati_g1 con 3 file, dati_g2, dati_g2_imprevisti, dati_g3 con 1005 voci estranee, g3_imprevisti con 500 documenti LX4) e Missione.xlsx."""
from html import escape
from pathlib import Path

OUT = Path(__file__).resolve().parent
SANS = "'Montserrat','Segoe UI Black','Segoe UI',system-ui,sans-serif"
MONO = "'IBM Plex Mono','Inconsolata','Cascadia Mono',Consolas,monospace"
INK, PAPER, PANEL, PANEL2, MUTED, RED, DARK, SOFT = '#161616', '#f3f0ea', '#ece8e0', '#fbf9f5', '#8a857c', '#5b0505', '#0e0e0e', '#a9a49b'
DAY_COLORS = {1: '#2f6f4f', 2: '#8a5a00', 3: '#1f4e79'}


def wrap(text, width):
    words, lines, line = text.split(), [], ''
    for w in words:
        if line and len(line) + 1 + len(w) > width:
            lines.append(line)
            line = w
        else:
            line = (line + ' ' + w).strip()
    return lines + ([line] if line else [])


class Svg:
    def __init__(self, width):
        self.width, self.parts = width, []

    def add(self, s):
        self.parts.append(s)

    def rect(self, x, y, w, h, fill, stroke=None, rx=3, sw=1.4):
        self.add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}"' + (f' stroke="{stroke}" stroke-width="{sw}"' if stroke else '') + '/>')

    def text(self, x, y, s, size=11, weight=400, fill=INK, anchor='start', font=SANS, spacing=None):
        sp = f' letter-spacing="{spacing}"' if spacing else ''
        self.add(f'<text x="{x}" y="{y}" font-family="{font}" font-size="{size}" font-weight="{weight}" fill="{fill}" text-anchor="{anchor}"{sp}>{escape(s)}</text>')

    def line(self, x1, y1, x2, y2, color=INK, sw=1.5, dash=None):
        self.add(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="{sw}"' + (f' stroke-dasharray="{dash}"' if dash else '') + '/>')

    def arrow_down(self, x, y1, y2):
        self.line(x, y1, x, y2)
        self.add(f'<path d="M{x} {y2} L{x - 5} {y2 - 9} L{x + 5} {y2 - 9} Z" fill="{INK}"/>')

    def arrow_right(self, x1, y, x2):
        self.line(x1, y, x2, y)
        self.add(f'<path d="M{x2} {y} L{x2 - 9} {y - 5} L{x2 - 9} {y + 5} Z" fill="{INK}"/>')

    def badge(self, x, y, label, fill=INK):
        w = 8 + 6.4 * len(label)
        self.rect(x, y - 11, w, 15, fill, rx=7.5, sw=0)
        self.text(x + w / 2, y, label, 9.5, 700, PAPER, 'middle', MONO)
        return w

    def header(self, subtitle, right1, right2, height=96):
        self.rect(0, 0, self.width, height, DARK)
        self.text(56, 48, 'LAZZARO', 28, 800, PAPER)
        self.text(56, 72, subtitle, 11, 400, SOFT, font=MONO, spacing=1.2)
        self.text(self.width - 56, 48, right1, 11, 700, '#e07a7a', 'end', MONO)
        self.text(self.width - 56, 72, right2, 10.5, 400, SOFT, 'end', MONO)

    def render(self, height, title):
        body = '\n'.join(self.parts)
        return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {self.width} {height}" width="{self.width}" height="{height}" role="img" aria-labelledby="titolo">\n'
                f'<title id="titolo">{escape(title)}</title>\n<rect width="{self.width}" height="{height}" fill="{PAPER}"/>\n{body}\n</svg>\n')


# Funzioni del chatbot (colonna destra): (id, titolo, sottotitolo, [(giornata, commento)])
FEATURES = [
    ('AVVIO E DATI', [
        ('F1', 'Documenti caricati a mano o da cartella', 'Con + o con il pulsante cartella (o --dati all\'avvio): txt, md, csv, json, xml, html, docx, pdf, xlsx e zip, con il giorno indicato.', []),
        ('F2', 'Embedding in cache su disco', 'Un brano già visto (impronta del testo) non si ricalcola: la cache .npy si legge al riavvio, i brani nuovi si aggiungono.', []),
    ]),
    ('RISPOSTA IN CHAT', [
        ('F3', 'Risposte con fonti citate [ID]', 'Ogni affermazione cita il documento; nomi, luoghi e orari vengono solo dai documenti, e se un dato manca lo dice.',
         [(1, 'le tre domande della traccia (cosa è successo, chi ha lasciato un messaggio, quali informazioni mancano)')]),
        ('F4', 'Domande di seguito e domande sulla chat', '«E lui?» diventa una domanda autonoma con i nomi degli scambi visibili; «cosa ti ho chiesto?» si risponde dalla cronologia, senza modello.', []),
        ('F5', 'Controllo di pertinenza della domanda', 'Regole, poi il modello con i soli titoli: se la domanda non è sul caso si conversa senza leggere i documenti. In parallelo alla ricerca.', []),
        ('F6', 'Cronologia per giorno', 'Ogni fonte dice il giorno in cui è arrivata; «giorno 2», «dal primo al terzo» ricevono i documenti di quei giorni.', []),
    ]),
    ('RICERCA NEI DOCUMENTI (RAG)', [
        ('F7', 'Documenti interi con pochi testi', 'Se tutti i brani stanno nel contesto si passano al modello interi, senza ricerca né embedding.',
         [(1, 'i tre file di dati_g1 stanno interi nel contesto')]),
        ('F8', 'Ricerca ibrida', 'Parole chiave (BM25) + significato (embedding) + HyDE quando serve, fusi con RRF pesata e scelti con MMR.',
         [(3, 'archivio da oltre mille file: si cercano i brani, non si passano tutti')]),
        ('F9', 'Pertinenza per contenuto', 'Un vettore per documento, k-means e profilo del caso, poi una rifinitura a due gruppi: gli estranei restano fuori dalle fonti.',
         [(3, 'dati_g3 contiene 1005 voci estranee al caso: il clustering le esclude, i documenti del caso restano')]),
        ('F10', 'Identificatori e salti tra documenti', 'Le parole rare nel caso (codici, pratiche) pesano doppio; dai documenti migliori si seguono codici e nomi rari di legame in legame.',
         [(3, 'i documenti di g3_imprevisti si collegano per codici: pratica, lotto, armadio')]),
        ('F11', 'Copie quasi identiche raggruppate', 'I brani con embedding quasi uguali occupano un solo posto e ne elencano le copie, così restano spazio e fonti per ciò che li smentisce.',
         [(3, 'g3_imprevisti ha molti bollettini quasi identici: la rettifica e il collaudo restano tra le fonti')]),
    ]),
    ('SICUREZZA, ELEMENTI E DEBUG', [
        ('F12', 'I documenti sono dati, non ordini', 'Il prompt lo dice e il modello non esegue le istruzioni dei documenti; se ne trova una la descrive. Nessun documento viene segnalato in anticipo.',
         [(2, 'il documento G2-04 dà ordini all\'assistente: non li esegue e, se rilevante, lo descrive')]),
        ('F13', 'Persone, luoghi, indizi ed eventi verificati', 'Dopo la risposta si estraggono voci con citazione copiata dal documento; nel pannello destro e nella bacheca va solo ciò che un documento del caso conferma.', []),
        ('F14', 'Modalità debug per il team', 'Etichette «sospetto» e «pertinente», conteggi e colori della RAG view restano nascosti; il pulsante Debug li riaccende.', []),
        ('F15', 'Scenario in un solo posto', 'Formati di ID e codici, regole del giorno, prefissi e nomi stanno in scenario.py; la descrizione del caso nel blocco SCENARIO di lazarus.py, separata dalle regole di comportamento.', []),
    ]),
]


def feature_block(svg, x, y, w, fid, title, subtitle, days):
    lines = wrap(subtitle, int((w - 28) / 6.3))
    comments = [(d, wrap(c, int((w - 100) / 6.3))) for d, c in days]
    h = 22 + 14 * len(lines) + sum(8 + 13 * len(c) for _, c in comments) + 12
    svg.rect(x, y, w, h, PANEL2, '#cfc9be', 3, 1)
    bw = svg.badge(x + 10, y + 17, fid)
    svg.text(x + 20 + bw, y + 17, title, 12, 800)
    cy = y + 34
    for ln in lines:
        svg.text(x + 14, cy, ln, 10.2, 400, '#4f4c47', font=MONO)
        cy += 14
    for day, comment in comments:
        label = f'Giorno {day}' if isinstance(day, int) else day
        color = DAY_COLORS.get(day, '#4a3f6b')
        cy += 8
        lw = svg.badge(x + 14, cy + 3, label, color)
        for i, ln in enumerate(comment):
            svg.text(x + 14 + lw + 8, cy + 3 + 13 * i, ln, 10, 400, color, font=MONO)
        cy += 13 * len(comment)
    return h


def flow():
    W, LEFT_X, CX, RIGHT_X, RIGHT_W = 1560, 40, 270, 720, 780
    s = Svg(W)
    s.header('FLUSSO DI UNA RISPOSTA · DIAGRAMMA DI FLUSSO', 'RAG VIEW', 'AVVIO · POST /api/chiedi · PERTINENZA DEI DOCUMENTI · SOLO LOCALE')
    state = {'y': 124}

    def band(title, right=''):
        y = state['y']
        s.rect(0, y, 690, 28, INK, sw=0, rx=0)               # solo la colonna del flusso: a destra restano i blocchi delle funzioni
        s.text(LEFT_X + 16, y + 19, title, 11.5, 700, PAPER, font=MONO, spacing=1.4)
        if right:
            s.text(676, y + 19, right, 10, 400, SOFT, 'end', MONO)
        state['y'] = y + 28 + 22

    def node(kind, title, sub='', feats=(), side=None, side_label='Sì', width=340):
        y = state['y']
        lines = wrap(sub, int((width - 30) / 6.2)) if sub else []
        tl = wrap(title, int((width - 30) / 7)) if kind != 'decision' else wrap(title, 22)
        if kind == 'decision':
            h = max(78, 38 + 14 * len(tl) + 14 * len(lines))
            half = width / 2
            s.add(f'<polygon points="{CX},{y} {CX + half},{y + h / 2} {CX},{y + h} {CX - half},{y + h / 2}" fill="{PANEL2}" stroke="{INK}" stroke-width="1.5"/>')
            ty = y + h / 2 - 7 * (len(tl) - 1) + 4
            for i, ln in enumerate(tl):
                s.text(CX, ty + 14 * i, ln, 11, 800, anchor='middle')
        else:
            h = 24 + 15 * len(tl) + 13 * len(lines) + 6
            rx = h / 2 if kind == 'terminal' else 3
            fill = INK if kind == 'terminal' else (PANEL if kind == 'io' else PANEL2)
            s.rect(CX - width / 2, y, width, h, fill, INK, rx, 1.4)
            color = PAPER if kind == 'terminal' else INK
            for i, ln in enumerate(tl):
                s.text(CX, y + 19 + 15 * i, ln, 12, 800, color, 'middle')
            for i, ln in enumerate(lines):
                s.text(CX, y + 19 + 15 * len(tl) + 13 * i + 2, ln, 9.6, 400, MUTED if kind != 'terminal' else SOFT, 'middle', MONO)
        fx = CX + width / 2 - 8
        for f in feats:
            s.badge(fx - 4 - 6.4 * len(f), y - 4, f)
            fx -= 14 + 6.4 * len(f)
        if side:
            sw_ = 190
            sx = CX + width / 2 + 40
            sl = wrap(side, int((sw_ - 16) / 6.3))
            sh = 14 + 13 * len(sl)
            sy = y + h / 2 - sh / 2
            s.arrow_right(CX + width / 2, y + h / 2, sx)
            s.text(CX + width / 2 + 6, y + h / 2 - 5, side_label, 10.5, 700, RED, font=MONO)
            s.rect(sx, sy, sw_, sh, PANEL, INK, 3, 1.2)
            for i, ln in enumerate(sl):
                s.text(sx + 8, sy + 15 + 13 * i, ln, 9.6, 400, INK, font=MONO)
        state['y'] = y + h
        return y, h

    def down(gap=26):
        s.arrow_down(CX, state['y'], state['y'] + gap)
        state['y'] += gap

    # --- A. Avvio
    band('A · AVVIO', 'avvia_interfaccia.bat → server.py')
    node('terminal', 'avvia_interfaccia.bat', 'python interfaccia/backend/server.py, con il modello e il modello degli embedding scelti nel .bat')
    down()
    node('process', 'Apri la porta 8765', 'la pagina risponde subito; i documenti si aggiungono con + o con la cartella (o --dati)', ['F1'])
    down()
    node('process', 'Leggi i file e dividili in brani', 'txt, md, csv, json, xml, html, docx, pdf, xlsx e zip; BM25, giorno e titolo di ogni documento', ['F1'])
    down()
    node('process', 'Embedding dei brani (in un thread)', 'bge-m3; i brani già in cache .npy non si ricalcolano', ['F2'])
    down()
    node('process', 'Pertinenza dei documenti', 'quando tutti gli embedding ci sono: vedi la sezione C', ['F9'])
    down()
    node('terminal', 'Pronto', 'elenco delle persone calcolato dopo la pertinenza; il modello di chat si precarica in memoria')
    state['y'] += 34

    # --- B. Domanda in chat
    band('B · DOMANDA IN CHAT', 'POST /api/chiedi')
    node('terminal', 'Domanda dell\'utente', 'server.api_chiedi · flusso NDJSON: strumento, fonti, token, fine, elementi')
    down()
    node('decision', 'Chiede della chat?', side='Risposta dalla cronologia, senza modello', side_label='Sì')
    s.text(CX + 8, state['y'] + 16, 'No', 10.5, 700, RED, font=MONO)
    down()
    node('process', 'Riscrittura della domanda di seguito', 'riscrivi_domanda: «e lui?» diventa autonoma, solo se sembra un seguito', ['F4'])
    down()
    node('decision', 'La domanda riguarda il caso?', side='No: si conversa senza leggere i documenti (controlla_pertinenza: regole, poi il modello)', side_label='No')
    s.text(CX + 8, state['y'] + 16, 'Sì', 10.5, 700, RED, font=MONO)
    state['y'] += 4
    down()
    node('process', 'Giorni chiesti o «tutti i file»', 'se la domanda nomina dei giorni, o tutti i file, e ci stanno nel contesto: quei brani interi', ['F6', 'F7'])
    down()
    node('process', 'Cerca i brani', 'BM25 + embedding + HyDE (se serve) + identificatori + domanda riscritta → RRF solo tra i pertinenti', ['F8', 'F10'])
    down()
    node('process', 'Copie raggruppate e MMR', 'i brani quasi identici occupano un posto solo; poi si scelgono i brani diversi tra loro', ['F11'])
    down()
    node('process', 'Segui i riferimenti', 'dai migliori brani del caso si seguono codici e nomi rari: i brani collegati si aggiungono (al massimo 6)', ['F10'])
    down()
    node('process', 'Costruisci il prompt', 'istruzioni di app_lazarus/lazarus.py (regole + blocco SCENARIO) + cronologia dei giorni + fonti con [ID] + domanda', ['F12', 'F15', 'F3'])
    down()
    node('io', 'Generazione in streaming', 'il modello di chat risponde con le sole fonti e cita [ID]; nessun documento è segnalato in anticipo', ['F3', 'F12'])
    down()
    node('process', 'Chiusura', 'persone citate, storico, evento «fine»')
    down()
    node('process', 'Dopo la risposta: luoghi, indizi, eventi', 'estratti dal modello o dalle regole e verificati con la citazione nei documenti del caso', ['F13'])
    down()
    node('terminal', 'Colonna destra e bacheca', 'ci va solo ciò che un documento del caso conferma')
    state['y'] += 34

    # --- C. Pertinenza dei documenti
    band('C · PERTINENZA DEI DOCUMENTI', 'pertinenza.py, dopo gli embedding')
    node('terminal', 'Archivio con gli embedding', 'sotto 40 documenti sono tutti pertinenti', ['F9'])
    down()
    node('process', 'Un vettore per documento', 'media degli embedding dei suoi brani, tolta la media dell\'archivio')
    down()
    node('process', 'k-means', 'K circa √(N/2), k-means++ con più ripartenze; il cluster più vicino al profilo del caso (parte RUOLO delle istruzioni)')
    down()
    node('process', 'Rifinitura a due gruppi', 'secondo k-means dentro i presi: si tiene il gruppo più vicino al profilo, se i due sono distinti', ['F9'])
    down()
    node('process', 'Rete di sicurezza', 'un documento scartato che somiglia al profilo più di ogni tenuto rientra')
    down()
    node('terminal', 'Documenti pertinenti', 'solo i loro brani servono per rispondere; la modalità debug li mostra', ['F14'])
    left_end = state['y'] + 40

    # --- colonna destra: blocchi delle funzioni
    ry = 124
    s.rect(RIGHT_X - 20, ry, RIGHT_W + 40, 28, INK, sw=0, rx=0)
    s.text(RIGHT_X - 4, ry + 19, 'LE FUNZIONI DEL CHATBOT E LE GIORNATE', 11.5, 700, PAPER, font=MONO, spacing=1.4)
    s.text(RIGHT_X + RIGHT_W + 16, ry + 19, 'F1 … F15: i badge sul flusso', 10, 400, SOFT, 'end', MONO)
    ry += 28 + 20
    for group, items in FEATURES:
        s.text(RIGHT_X, ry + 10, group, 10.5, 700, RED, font=MONO, spacing=1.2)
        s.line(RIGHT_X, ry + 16, RIGHT_X + RIGHT_W, ry + 16, '#cfc9be', 1)
        ry += 26
        for fid, title, subtitle, days in items:
            ry += feature_block(s, RIGHT_X, ry, RIGHT_W, fid, title, subtitle, days) + 10
        ry += 10
    ry += 6
    nota = ('Le giornate sono indicate solo dove sono certe: il commento di app_lazarus/lazarus.py (Giorno 1: tutti i testi, dal giorno 3 il recupero RAG), '
            'Missione.xlsx per il Giorno 1 e le cartelle dei dati (dati_g1 con 3 file, dati_g2_imprevisti, dati_g3 con 1005 voci estranee, g3_imprevisti con 500 documenti). '
            'Dove non c\'è una giornata, la funzione serve a tutte.')
    for ln in wrap(nota, 120):
        s.text(RIGHT_X, ry, ln, 9.8, 400, MUTED, font=MONO)
        ry += 13
    height = int(max(left_end, ry) + 30)
    return s.render(height, 'Flusso di Lazzaro: diagramma di flusso con le funzioni del chatbot e le giornate')


if __name__ == '__main__':
    (OUT / 'flusso_algoritmo.svg').write_text(flow(), encoding='utf-8', newline='\r\n')
    print('flusso_algoritmo.svg scritto in', OUT)
