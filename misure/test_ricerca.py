"""Prove automatiche della ricerca, senza il modello vero e senza Ollama (embedding finti):

    python misure/test_ricerca.py

Coprono: identificatori nella fusione, embedding parziali e salvati a gruppi, equivalenza tra la ricerca con numpy e quella in Python puro,
riconoscimento e riscrittura dei seguiti. Le misure con i documenti e il modello veri sono in misura_ricerca.py."""
import shutil
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

QUI = Path(__file__).resolve().parent
sys.path.insert(0, str(QUI.parent / 'interfaccia' / 'backend'))

import config as cfg  # noqa: E402
import indice as indice_mod  # noqa: E402
import motore  # noqa: E402


def embed_finto(testi):
    """Vettore di parole in 64 dimensioni: testi con parole in comune sono vicini."""
    out = []
    for t in testi:
        v = [0.0] * 64
        for w in t.lower().split():
            v[zlib.crc32(w.encode()) % 64] += 1.0
        out.append(v)
    return out


def tutti_pertinenti(medi, _profilo):
    return set(medi), {d: 1.0 for d in medi}, 'prova: tutti pertinenti', None


class IndiceDiProva(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / 'caricati').mkdir()
        for i in range(60):             # più di SOGLIA_BRANI: la ricerca non passa tutto al modello
            (self.tmp / 'caricati' / f'F{i:02d}.txt').write_text(
                f'ID F{i:02d}\nRapporto {i}\n\nrapporto di routine numero {i} senza novità nel turno di guardia', encoding='utf-8')
        (self.tmp / 'caricati' / 'K01.txt').write_text('ID K01\nVerbale di consegna\n\nLa pratica ZK-7731 è stata consegnata al magazzino nord', encoding='utf-8')
        self.salvati = (indice_mod.CARTELLA_CARICATI, indice_mod.CARTELLA_REGISTRO, indice_mod.embed, indice_mod.raggruppa_pertinenti)
        indice_mod.CARTELLA_CARICATI, indice_mod.CARTELLA_REGISTRO = self.tmp / 'caricati', self.tmp / 'registro'
        indice_mod.embed, indice_mod.raggruppa_pertinenti = embed_finto, tutti_pertinenti
        self.indice = indice_mod.Indice()
        self.indice.ricarica()

    def tearDown(self):
        indice_mod.CARTELLA_CARICATI, indice_mod.CARTELLA_REGISTRO, indice_mod.embed, indice_mod.raggruppa_pertinenti = self.salvati
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_identificatore_e_primo(self):
        self.indice.calcola_embedding()
        self.assertTrue(self.indice.embedding_ok)
        strumenti = []
        fonti = self.indice.cerca('Cosa risulta su ZK-7731?', strumenti)
        self.assertEqual(fonti[0]['doc'], 'K01')
        self.assertIn('cerca_identificatori', [s['nome'] for s in strumenti])
        senza = []                      # una domanda senza parole rare non produce la classifica degli identificatori
        self.indice.cerca('rapporto di routine', senza)
        self.assertNotIn('cerca_identificatori', [s['nome'] for s in senza])

    def escludi_k01(self):
        with self.indice.lock:
            self.indice.documenti['K01']['consultazione'] = True
            for b in self.indice.brani:
                b['consultazione'] = b['doc'] == 'K01'
            self.indice.df_caso = None

    def test_codice_raro_recupera_un_documento_escluso(self):
        self.indice.calcola_embedding()
        self.escludi_k01()              # il clustering ha sbagliato: K01 è tra gli estranei
        strumenti = []
        fonti = self.indice.cerca('Cosa risulta su ZK-7731?', strumenti)
        k01 = [f for f in fonti if f['doc'] == 'K01']
        self.assertTrue(k01 and k01[0].get('recuperato') and not k01[0]['consultazione'])

    def test_ricarica_tiene_la_pertinenza_dei_documenti_gia_visti(self):
        self.escludi_k01()
        self.indice.ricarica()          # come dopo un caricamento: il clustering nuovo non è ancora finito
        self.assertTrue(self.indice.documenti['K01']['consultazione'])
        self.assertTrue(all(b['consultazione'] for b in self.indice.brani if b['doc'] == 'K01'))

    def test_embedding_parziali_e_cache_a_gruppi(self):
        self.indice.GRUPPO_EMBEDDING, self.indice.SALVA_OGNI = 8, 2
        chiamate = []

        def a_scatti(testi):
            chiamate.append(len(testi))
            if len(chiamate) > 3:
                raise RuntimeError('Ollama non risponde')
            return embed_finto(testi)
        indice_mod.embed = a_scatti
        self.indice.calcola_embedding()
        self.assertFalse(self.indice.embedding_ok)              # incompleti: la pertinenza non si calcola
        self.assertTrue(self.indice.embedding_usabile)          # ma quelli pronti servono alla ricerca
        self.assertEqual(sum(self.indice.emb_presenti), 24)
        self.assertTrue(self.indice.file_cache.exists())        # salvati anche dopo l'errore
        indice_mod.embed = embed_finto
        strumenti = []
        self.assertTrue(self.indice.cerca('rapporto numero 3', strumenti))
        self.assertIn('embedding pronto su', next(s['dettaglio'] for s in strumenti if s['nome'] == 'cerca_semantica'))
        riparte = []
        indice_mod.embed = lambda testi: (riparte.append(len(testi)), embed_finto(testi))[1]
        self.indice.calcola_embedding()                         # rifà solo i mancanti
        self.assertEqual(sum(n for n in riparte if n > 1), 61 - 24)          # (la richiesta da 1 è il profilo del caso, per la pertinenza)
        self.assertTrue(self.indice.embedding_ok)
        nuovo = indice_mod.Indice()                             # riavvio: la cache salvata basta, nessuna chiamata
        nuovo.ricarica()
        self.assertTrue(nuovo.embedding_ok)

    @unittest.skipIf(indice_mod.np is None, 'numpy non installato')
    def test_numpy_e_python_puro_danno_lo_stesso_ordine(self):
        self.indice.calcola_embedding()
        vettore = embed_finto(['pratica ZK-7731 magazzino nord'])[0]
        brani, presenti = list(self.indice.brani), list(self.indice.emb_presenti)
        con_numpy = self.indice.classifica_per_significato(vettore, brani, self.indice.matrice, presenti)
        senza_numpy = self.indice.classifica_per_significato(vettore, brani, None, presenti)
        self.assertEqual(con_numpy[0][0], senza_numpy[0][0])
        for a, b in zip(con_numpy[1][:10], senza_numpy[1][:10]):
            self.assertAlmostEqual(a, b, places=4)

    def test_brani_senza_embedding_non_sono_in_classifica(self):
        self.indice.calcola_embedding()
        brani = list(self.indice.brani)
        presenti = [i % 2 == 0 for i in range(len(brani))]
        indici, punti = self.indice.classifica_per_significato(embed_finto(['rapporto'])[0], brani, None, presenti)
        self.assertTrue(all(presenti[i] for i in indici))
        self.assertEqual(len(indici), sum(presenti))
        self.assertEqual(punti, sorted(punti, reverse=True))


class SeguitiTest(unittest.TestCase):
    STORIA = [{'domanda': 'Chi ha aperto la pratica?', 'risposta': 'Luca Serra [ARCH-3097].'}]

    def setUp(self):
        self.originale = motore.riscrivi_con_modello

    def tearDown(self):
        motore.riscrivi_con_modello = self.originale

    def test_riconosce_i_seguiti(self):
        for d in ('E lui?', 'E dopo cosa è successo?', 'Dov\'era in quel momento?', 'Perché lo accusavano?', 'Chi altro c\'era?', 'Quindi è colpevole?'):
            self.assertTrue(motore.sembra_seguito(d), d)
        for d in ('Cosa dice la circolare interna sui rifugi?', 'In quale settore si trova il contenitore LUME?', 'Chi ha chiesto di fermare il progetto?'):
            self.assertFalse(motore.sembra_seguito(d), d)

    def test_senza_storia_o_senza_modello_non_chiama_nulla(self):
        def vietato(*_):
            raise AssertionError('il modello non doveva essere chiamato')
        motore.riscrivi_con_modello = vietato
        strumenti = []
        self.assertEqual(motore.riscrivi_domanda('m', 'E lui?', [], strumenti), 'E lui?')                   # nessuno scambio
        self.assertEqual(motore.riscrivi_domanda(None, 'E lui?', self.STORIA, strumenti), 'E lui?')         # nessun modello
        self.assertEqual(motore.riscrivi_domanda('m', 'E lui?', self.STORIA, strumenti, demo=True), 'E lui?')
        self.assertEqual(motore.riscrivi_domanda('m', 'Cosa dice la circolare sui rifugi?', self.STORIA, strumenti), 'Cosa dice la circolare sui rifugi?')
        self.assertEqual(strumenti, [])

    def test_riscrive_e_pulisce(self):
        motore.riscrivi_con_modello = lambda *_: 'Domanda: Cosa voleva Luca Serra [ARCH-3097]?\nPerché è importante.'
        strumenti = []
        self.assertEqual(motore.riscrivi_domanda('m', 'E lui cosa voleva?', self.STORIA, strumenti), 'Cosa voleva Luca Serra?')
        self.assertEqual([s['nome'] for s in strumenti], ['riscrivi_domanda'])
        self.assertIn('→', strumenti[0]['dettaglio'])

    def test_domanda_gia_autonoma_resta_uguale(self):
        motore.riscrivi_con_modello = lambda _m, d, _s: d.upper()          # stesso testo a parte le maiuscole
        strumenti = []
        self.assertEqual(motore.riscrivi_domanda('m', 'E quindi?', self.STORIA, strumenti), 'E quindi?')
        self.assertEqual(strumenti[0]['dettaglio'], 'domanda già autonoma')

    def test_errore_del_modello_o_risposta_inutilizzabile(self):
        def guasto(*_):
            raise RuntimeError('Ollama non risponde')
        motore.riscrivi_con_modello = guasto
        strumenti = []
        self.assertEqual(motore.riscrivi_domanda('m', 'E lui?', self.STORIA, strumenti), 'E lui?')
        self.assertIn('errore', strumenti[0]['dettaglio'])
        motore.riscrivi_con_modello = lambda *_: 'x' * (cfg.RISCRIVI_MAX_CARATTERI + 1)      # troppo lunga: non è una domanda
        strumenti = []
        self.assertEqual(motore.riscrivi_domanda('m', 'E lui?', self.STORIA, strumenti), 'E lui?')
        motore.riscrivi_con_modello = lambda *_: ''
        self.assertEqual(motore.riscrivi_domanda('m', 'E lui?', self.STORIA, []), 'E lui?')

    def test_legge_solo_gli_ultimi_scambi_validi(self):
        viste = []
        motore.riscrivi_con_modello = lambda _m, _d, scambi: (viste.append(scambi), 'Cosa voleva Luca Serra?')[1]
        storia = [{'domanda': 'vecchia', 'risposta': 'x'}, {'domanda': 'sbagliata', 'risposta': 'y', 'errore': 'boom'},
                  {'domanda': 'meta', 'risposta': 'z', 'meta': 1}, *self.STORIA]
        motore.riscrivi_domanda('m', 'E lui?', storia, [])
        self.assertIn('Chi ha aperto la pratica?', viste[0])
        self.assertNotIn('sbagliata', viste[0])
        self.assertNotIn('meta', viste[0])


class CronologiaTest(unittest.TestCase):
    def indice_finto(self, per_giorno):
        import threading
        from types import SimpleNamespace
        docs = {f'D{g}_{i}': {'id': f'D{g}_{i}', 'id_interno': f'X{g}-{i}', 'giorno': g, 'consultazione': False}
                for g, n in per_giorno.items() for i in range(n)}
        return SimpleNamespace(lock=threading.RLock(), documenti=docs)

    def test_pochi_documenti_si_elencano(self):
        nota = motore.nota_giorni(self.indice_finto({1: 3, 2: cfg.NOTA_GIORNI_MAX_ID}))
        self.assertIn('X1-0, X1-1, X1-2', nota)
        self.assertIn(f'X2-{cfg.NOTA_GIORNI_MAX_ID - 1}', nota)

    def test_troppi_documenti_non_riempiono_il_contesto(self):
        nota = motore.nota_giorni(self.indice_finto({1: 3, 3: 700}))
        self.assertIn('giorno 3: 700 documenti', nota)
        self.assertNotIn('X3-5', nota)
        self.assertLess(len(nota), 400)               # 700 ID erano ~7400 caratteri, ~5700 token
        self.assertIn('X1-0', nota)                   # i giorni piccoli restano elencati


class GiorniEMmrTest(unittest.TestCase):
    def test_intervalli_di_giorni(self):
        casi = {'cosa è successo fino al giorno 2 compreso?': {1, 2}, 'cosa è successo fino al giorno 2 escluso?': {1},
                'entro il giorno 3 cosa è successo?': {1, 2, 3}, 'riassumi i primi due giorni': {1, 2},
                'cosa è successo dal primo al terzo giorno?': {1, 2, 3}, 'cosa è successo il giorno 2?': {2},
                'cosa è successo il primo e il terzo giorno?': {1, 3}, 'chi è Luca Serra?': set(),
                'cosa è successo il giorno 2 alle 14?': {2}, 'il giorno 3 alle 14:30 chi era al varco?': {3}}
        for domanda, atteso in casi.items():
            self.assertEqual(motore.giorni_chiesti(domanda), atteso, domanda)

    @unittest.skipIf(indice_mod.np is None, 'numpy non installato')
    def test_mmr_toglie_i_doppioni_e_lascia_il_brano_diverso(self):
        np = indice_mod.np
        matrice = np.array([[1, 0], [1, 0], [1, 0], [0.6, 0.8], [1, 0]], dtype=np.float32)    # 0, 1, 2, 4 identici; 3 diverso
        punti = {0: 1.0, 1: 0.95, 2: 0.9, 3: 0.7, 4: 0.6}
        candidati = [0, 1, 2, 3, 4]
        self.assertEqual(indice_mod.diversifica(candidati, punti, matrice, 1.0, 2), [0, 1])        # lam = 1: come prima
        self.assertEqual(indice_mod.diversifica(candidati, punti, matrice, 0.5, 2), [0, 3])        # il doppione cede il posto al brano diverso
        self.assertEqual(indice_mod.diversifica(candidati, punti, None, 0.5, 2), [0, 1])           # senza embedding: come prima
        self.assertEqual(indice_mod.diversifica(candidati, punti, matrice, 0.5, 9), candidati)     # pochi candidati: tutti


@unittest.skipIf(indice_mod.np is None, 'numpy non installato')
class SaltoSuiCodiciTest(unittest.TestCase):
    """Un codice comune a molti brani quasi uguali: il salto deve portare il brano diverso, non altre varianti."""

    def setUp(self):
        import threading
        from types import SimpleNamespace
        np = indice_mod.np
        self.brani = [{'doc': f'D{i}', 'n': 0, 'testo': f'Esito riferito a XY-12 numero {i}', 'id_interno': None, 'consultazione': False,
                       'sospetto': None, 'giorno': None} for i in range(7)]            # 0 = fonte, 1-5 = varianti, 6 = brano diverso
        vettori = np.array([[1, 0]] * 6 + [[0, 1]], dtype=np.float32)
        self.indice = SimpleNamespace(lock=threading.RLock(), brani=self.brani, matrice=vettori)
        self.fonti = [motore.fonte_da_brano(self.brani[0], 1.0)]
        self.salvato = motore.embed
        motore.embed = lambda _testi: [[0.8, 0.6]]            # la domanda assomiglia alle varianti più che al brano diverso

    def tearDown(self):
        motore.embed = self.salvato

    def test_con_embedding_porta_il_brano_diverso(self):
        strumenti = []
        risultato = motore.segui_riferimenti(self.fonti, self.brani, strumenti, self.indice, 'domanda')
        self.assertIn('D6', [f['doc'] for f in risultato])
        self.assertLessEqual(len(risultato) - 1, motore.BRANI_PER_CODICE)
        self.assertIn('XY-12', strumenti[0]['dettaglio'])

    def test_senza_embedding_come_prima(self):
        risultato = motore.segui_riferimenti(self.fonti, self.brani, [], None, None)
        self.assertEqual([f['doc'] for f in risultato][:1], ['D0'])
        self.assertNotIn('D6', [f['doc'] for f in risultato])          # i primi in ordine di archivio, e il codice è già «coperto»

    def test_non_aggiunge_piu_del_tetto(self):
        brani = [{**b, 'testo': b['testo'] + ' AB-34'} for b in self.brani]
        risultato = motore.segui_riferimenti([motore.fonte_da_brano(brani[0], 1.0)], brani, [], self.indice, 'domanda')
        self.assertLessEqual(len(risultato) - 1, motore.MAX_BRANI_AGGIUNTI)


class SaltoSuiNomiTest(unittest.TestCase):
    """Il legame tra due documenti può essere un nome raro di luogo o di persona, non un codice."""

    def brano(self, doc, testo):
        return {'doc': doc, 'n': 0, 'testo': testo, 'id_interno': None, 'consultazione': False, 'sospetto': None, 'giorno': None}

    def test_segue_un_nome_raro_e_ignora_uno_comune(self):
        fonte = self.brano('A', 'Al prossimo controllo serviranno persona e pratica. Il deposito Bastione Cavo usa il settore H3. Il Ripetitore Rame è attivo.')
        bersaglio = self.brano('B', 'Il contenitore è stato spostato nel deposito Bastione Cavo.')
        altro = self.brano('C', 'Rapporto senza nomi di luoghi.')
        comuni = [self.brano(f'H{i}', 'Il Ripetitore Rame è attivo e pronto.') for i in range(motore.ENTITA_MAX_BRANI + 5)]
        caso = [fonte, bersaglio, altro, *comuni]
        strumenti = []
        risultato = motore.segui_riferimenti([motore.fonte_da_brano(fonte, 1.0)], caso, strumenti)
        docs = [f['doc'] for f in risultato]
        self.assertIn('B', docs)                                             # «Bastione Cavo» compare in due brani: è un legame
        self.assertNotIn('C', docs)
        self.assertFalse([d for d in docs if d.startswith('H')])             # «Ripetitore Rame» è ovunque: non collega nulla
        self.assertIn('Bastione Cavo', strumenti[0]['dettaglio'])

    def test_entita_in_toglie_la_parola_generica(self):
        self.assertIn('Bastione Cavo', motore.entita_in('Il deposito Bastione Cavo usa il settore H3.'))
        self.assertIn('Luca Serra', motore.entita_in('Luca Serra ha chiesto di fermare il progetto.'))

    def test_i_nomi_comuni_non_consumano_il_limite_dei_legami(self):
        comuni = ' '.join(f'Il {n} Rossi ha parlato.' for n in ('Mario', 'Luigi', 'Paolo', 'Marco', 'Carlo', 'Anna', 'Elena', 'Sara'))
        fonte = self.brano('A', comuni + ' Poi il deposito Bastione Cavo è stato aperto.')
        bersaglio = self.brano('B', 'Spostamento nel deposito Bastione Cavo.')
        piene = [self.brano(f'H{i}', comuni) for i in range(motore.ENTITA_MAX_BRANI + 3)]
        risultato = motore.segui_riferimenti([motore.fonte_da_brano(fonte, 1.0)], [fonte, bersaglio, *piene], [])
        self.assertIn('B', [f['doc'] for f in risultato])


if __name__ == '__main__':
    unittest.main(verbosity=2)
