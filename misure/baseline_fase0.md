# Baseline prima degli interventi (22/10/2026)

Fonte: `interfaccia/registro/storico.jsonl` (7 domande reali, nessun test eseguito). Copia del progetto: `../Lazarus_Protocol_locale_backup_fase0`.
Tempi in ms. `1a parola` = dalla domanda al primo token; `tot` = fino alla fine.

| Domanda | ricerca | 1a parola | tot | token | prompt |
|---|---|---|---|---|---|
| cosa è successo il giorno 2? | 1693 | 3988 | 13831 | 494 | 2640 |
| cosa è successo nei rifugi? | 4362 | 7246 | 12856 | 224 | 3525 |
| dove si trova LUME? | 4192 | 7425 | 12710 | 238 | 4095 |
| quale ripetitore e quale circuito bisonga usa | 3016 | 6057 | 15033 | 417 | 3803 |
| dove si trova il modulo nella custodia scherm… | 3793 | 6450 | 15544 | 418 | 3272 |
| scusami era il corriere spina, dove si trova… | 3734 | 6017 | 9690 | 136 | 2790 |
| Dove si trova il modulo nella custodia scherm… | 1310 | 4281 | 10808 | 303 | 3032 |

Costo per passo (ms, intervallo osservato):

- controlla_pertinenza con il modello: 1690-1730 (quando le regole non bastano); 3-5 con le regole
- cerca_parole_chiave (BM25): 9-20
- cerca_semantica (embedding domanda + prodotto): 33-790
- cerca_hyde: 2095-2576 (quando scatta)
- segui_riferimenti: 102-497
- prima parola dopo la ricerca: circa 2900-3300 (elaborazione del prompt, nessuna cache)
- genera_risposta: 5058-10825
- estrai_persone: 896-1311 (dopo l'ultimo token, ma prima di "fine")
