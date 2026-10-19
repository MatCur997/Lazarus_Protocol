"""Volti delle persone: ogni persona trovata riceve una foto fissa, scelta in base al sesso dedotto dal nome.

Le foto e il riquadro del viso stanno in frontend/volti/volti.json. L'assegnazione si salva in
registro/volti.json: una volta data, una foto resta a quella persona anche se se ne aggiungono altre.
Alla prima comparsa si sceglie la foto meno usata del sesso giusto (finché ce ne sono, ognuno ha la sua)."""
import json
import threading
import unicodedata

import config as cfg

# Nomi italiani in -a che sono maschili, e nomi in -e/-i/consonante che sono femminili.
MASCHILI_IN_A = set('''luca andrea nicola mattia elia enea battista gianluca giosuè joshua tobia isaia geremia
zaccaria ezechiele barnaba noah sasha nikita akira'''.split())
FEMMINILI = set('''irene beatrice alice adele agnese rachele matilde clarice clotilde cloe chloe ester esther
miriam ingrid carmen dolores mercedes ruth judith giuditta noemi naomi suzanne marion rosmary rosemary
kim jennifer ellen helen eleonore ines inès abigail iris nives vivien lilian gwen daphne zoe zoé'''.split())
TITOLI_F = {'signora', 'signorina', 'dottoressa', 'professoressa', 'ispettrice', 'suor', 'madre', 'sorella', 'zia', 'nonna'}
TITOLI_M = {'signor', 'signore', 'dottor', 'dottore', 'professor', 'professore', 'don', 'padre', 'fratello', 'zio', 'nonno', 'ispettore'}


def sesso_dal_nome(nome):
    """'f' o 'm' dal primo nome (o da un titolo come "signora"). Nel dubbio 'm'."""
    parole = [unicodedata.normalize('NFKD', p).encode('ascii', 'ignore').decode().lower()
              for p in nome.replace('.', ' ').split()]
    for p in parole:
        if p in TITOLI_F:
            return 'f'
        if p in TITOLI_M:
            return 'm'
    primo = parole[0] if parole else ''
    if primo in MASCHILI_IN_A:
        return 'm'
    if primo in FEMMINILI or (primo.endswith('a') and len(primo) > 2):
        return 'f'
    return 'm'


class Volti:
    def __init__(self):
        self.lock = threading.Lock()
        self.file_assegnati = cfg.CARTELLA_REGISTRO / 'volti.json'
        try:
            self.foto = json.loads((cfg.STATICI / 'volti' / 'volti.json').read_text(encoding='utf-8'))['volti']
        except (OSError, ValueError, KeyError):
            self.foto = []                        # nessuna foto: le pagine mostrano le iniziali
        try:
            self.assegnati = json.loads(self.file_assegnati.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            self.assegnati = {}                   # nome -> id della foto

    def _pubblica(self, foto):
        return {'file': f"volti/{foto['file']}", 'larghezza': foto['larghezza'], 'altezza': foto['altezza'],
                'viso': foto['viso'], 'sesso': foto['sesso']}

    def volto_di(self, nome):
        """La foto di questa persona (la assegna e la salva alla prima richiesta). None se non ci sono foto."""
        if not self.foto:
            return None
        with self.lock:
            per_id = {f['id']: f for f in self.foto}
            if self.assegnati.get(nome) in per_id:
                return self._pubblica(per_id[self.assegnati[nome]])
            sesso = sesso_dal_nome(nome)
            candidate = [f for f in self.foto if f['sesso'] == sesso] or self.foto
            usi = {f['id']: 0 for f in candidate}
            for scelto in self.assegnati.values():
                if scelto in usi:
                    usi[scelto] += 1
            foto = min(candidate, key=lambda f: usi[f['id']])     # la meno usata; a parità, la prima dell'elenco
            self.assegnati[nome] = foto['id']
            cfg.CARTELLA_REGISTRO.mkdir(exist_ok=True)
            self.file_assegnati.write_text(json.dumps(self.assegnati, ensure_ascii=False, indent=1), encoding='utf-8')
            return self._pubblica(foto)
