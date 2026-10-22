# Investigatore LAZZARO: architettura

Guida per chi deve mantenere o studiare il codice. Tutto gira **in locale**: il browser parla solo con il server
Python su `127.0.0.1`, e il server parla solo con Ollama su `127.0.0.1:11434`. Nessuna libreria esterna nel frontend,
solo la libreria standard (più `requests`) nel backend.

```
 BROWSER (frontend)                    SERVER PYTHON (backend)                         OLLAMA
 interfaccia/frontend/                 interfaccia/backend/  +  app_lazarus/            gemma4:12b, bge-m3
 ┌──────────────────────┐   HTTP/JSON  ┌──────────────────────────────────────┐  HTTP  ┌──────────────┐
 │ index.html  (chat)   │ ───────────▶ │ server.py   rotte /api/...           │ ─────▶ │ /api/chat    │
 │ persona.html (schede)│ ◀─────────── │ motore.py   passi di una risposta    │ ◀───── │ /api/embed   │
 │ bacheca.html         │  NDJSON per  │ indice.py · testo.py · storico.py    │        └──────────────┘
 └──────────────────────┘  la chat     │ app_lazarus/lazarus.py  istruzioni,  │
                                       │   pertinenza, modello                │
                                       └──────────────────────────────────────┘
                                         dati: interfaccia/registro/, interfaccia/caricati/
```

## Cartelle

| Cartella | Lato | Contenuto |
|---|---|---|
| `app_lazarus/` | Backend | `lazarus.py`: modello, istruzioni del modello, controllo di pertinenza: l'**unico** posto dove sta il prompt (`config.py` non ne ha più una copia né lo legge con `ast`: importa `lazarus.py`, che usa `requests` solo quando serve). Funziona anche da solo come chat nel terminale (`uv run lazarus.py`). |
| `interfaccia/backend/` | Backend | Server HTTP e logica dell'interfaccia (Python). |
| `interfaccia/frontend/` | Frontend | Pagine HTML, stile CSS, script JavaScript del browser. |
| `interfaccia/registro/` | Dati | `storico.jsonl` (domande e risposte), `elementi.jsonl` (indizi, eventi, luoghi trovati), cache degli embedding, esclusioni. |
| `interfaccia/caricati/` | Dati | Documenti caricati a mano con **+** o con il pulsante cartella. Nessun documento è caricato in automatico all'avvio (solo con `--dati <cartella>`). |
| `lazzaro/` | Dati | `diario_chat.jsonl`: il registro della squadra, ogni domanda e risposta, mai cancellato dall'interfaccia e mai letto come documento. |
| `avvia_interfaccia.bat` | Avvio | Sceglie il modello e avvia `interfaccia/backend/server.py`. |

## Backend: file per file

| File | Responsabilità | Funzioni principali |
|---|---|---|
| `app_lazarus/lazarus.py` | Istruzioni del modello e regole di base, condivise con la chat nel terminale | `ISTRUZIONI`, `ISTRUZIONI_CONVERSAZIONE`, `classifica_domanda` (pertinenza), `e_conversazione`, `prepara_richiesta` (ogni domanda indipendente, contesto mai pieno), costanti `MODELLO`, `MAX_TOKEN`, `NUM_CTX`, `TEMPERATURA` |
| `backend/config.py` | Impostazioni, percorsi, collegamento con `app_lazarus` | `istruzioni_correnti`, `classifica_domanda`, `STRUMENTI` |
| `backend/server.py` | Rotte HTTP: riceve le richieste del browser e risponde in JSON | `Gestore.api_*`, `main` |
| `backend/motore.py` | I passi di una risposta | `controlla_pertinenza` → `cerca_fonti` → `costruisci_messaggi` → `genera` |
| `backend/modello.py` | Chiamate a Ollama | `genera_ollama` (streaming), `embed`, `precarica`, `genera_demo` |
| `backend/indice.py` | Documenti in memoria, ricerca BM25 + embedding, indice delle persone | `Indice.ricarica`, `Indice.cerca`, `Indice.persone_in` |
| `backend/testo.py` | Funzioni sul testo italiano | `parole`, `frasi`, `estrai_nomi`, `estrai_luoghi`, `estrai_voci_nominate` (indizi/ipotesi/eventi con riscontro) |
| `backend/storico.py` | Storico delle domande e degli elementi trovati | `Storico.aggiungi`, `raccogli`, `elimina`, `escludi` |
| `backend/volti.py` | Foto fissa per ogni persona, scelta in base al sesso dedotto dal nome; l'assegnazione si salva in `registro/volti.json` | `sesso_dal_nome`, `Volti.volto_di` |

## Frontend: file per file

| File | Responsabilità |
|---|---|
| `frontend/index.html` | Pagina principale: tre pannelli (storico/strumenti/tempi/documenti · chat · elementi dell'indagine). |
| `frontend/app.js` | Logica della pagina principale: carica i dati, disegna i pannelli, invia le domande e mostra la risposta mentre arriva. |
| `frontend/persona.html` | Schede di dettaglio ("fascicolo aperto"): persona, luogo, indizio, evento, documento, risultati della ricerca. |
| `frontend/bacheca.html` | **Accesso Segreto**: bacheca con le letterine degli indizi e i fili rossi. |
| `frontend/comune.js` | Funzioni condivise da tutte le pagine: `$`, `esc`, `markdown`, `senzaMarkdown`, `rispostaHtml`, `voltoHtml` (miniatura rotonda del volto). |
| `frontend/volti/` | Foto delle persone e `volti.json` (elenco con sesso, misure e riquadro del viso). |
| `frontend/tema.js` | Tema chiaro (carta) / scuro (grigio e panna) e **modalità debug**, entrambi ricordati nel browser. Caricato in `<head>` di ogni pagina. |
| `frontend/app.css` | Stile "fascicolo" di tutte le pagine. **I colori stanno solo nelle variabili in cima** (`:root` = chiaro, `:root[data-tema="scuro"]` = scuro). |

## Le funzioni dell'applicazione, lato per lato

| Funzione | Frontend | API | Backend |
|---|---|---|---|
| Fare una domanda | `app.js` → `chiedi`, `completaRisposta` | `POST /api/chiedi` (risposta a righe NDJSON: `strumento`, `fonti`, `token`, `fine`, `elementi`) | `server.api_chiedi` → `motore.*` → `modello.genera_ollama` |
| Riconoscere domande non pertinenti | (mostrato in "strumenti usati") | dentro `/api/chiedi` | `motore.controlla_pertinenza` → `lazarus.classifica_domanda` |
| Indizi, ipotesi, eventi, luoghi della risposta | `app.js` → `mostraElementi`, colonna destra `disegnaElementi` | `GET /api/elementi`, `GET /api/elemento` | `testo.estrai_voci_nominate`, `testo.estrai_luoghi`, `storico.raccogli` |
| Persone | `app.js` → `vocePersona`; `persona.html` → `paginaPersona` | `GET /api/persone`, `GET /api/persona`, `POST /api/persona/escludi` | `indice.persone_in`, `Indice._indicizza_persone` |
| Schede di dettaglio | `persona.html` | `GET /api/persona`, `/api/elemento`, `/api/documento`, `/api/cerca` | `server.api_*`, `Gestore.passaggi` |
| Bacheca (Accesso Segreto) | `bacheca.html` (disposizione, puntine colorate per tipo, fili, riquadro al passaggio del mouse) | `GET /api/bacheca` | `server.api_bacheca` (nodi = indizi, ipotesi, eventi verificati, persone trovate e luoghi presenti nei documenti; fili = i 2 collegamenti più forti per voce + ogni persona e ogni luogo verso le voci che li riguardano) |
| Volti delle persone | `comune.js` → `voltoHtml`; colonna destra, scheda persona, riquadro della bacheca | campo `volto` in `/api/persone`, `/api/persona`, `/api/bacheca` | `volti.Volti` |
| Storico | `app.js` → `disegnaStorico` | `GET /api/storico`, `POST /api/storico/elimina`, `POST /api/storico/svuota` | `storico.Storico` |
| Caricare file | `app.js` → `carica` | `POST /api/carica`, `POST /api/rimuovi` | `server.api_carica`, `indice.testo_da_bytes` |
| Stato, modelli, tempi, strumenti | `app.js` → `disegnaStato`, `disegnaTempi`, `disegnaStrumenti` | `GET /api/stato`, `/api/strumenti`, `POST /api/modello` | `server.api_stato`, `modello.modelli_disponibili` |
| Tema chiaro/scuro | `tema.js`, variabili in `app.css` | — | — |

## Dove si cambia cosa

- **Come risponde il modello** (tono, formato, sezioni Eventi/Indizi/Ipotesi): `ISTRUZIONI` in `app_lazarus/lazarus.py`.
  Se cambi il formato delle sezioni, aggiorna anche `estrai_voci_nominate` in `backend/testo.py`, che le legge.
- **Velocità e lunghezza**: `MAX_TOKEN`, `NUM_CTX`, `TEMPERATURA` in `lazarus.py`.
- **Cosa conta come "non pertinente"**: `PAROLE_CONVERSAZIONE` e `ISTRUZIONI_PERTINENZA` in `lazarus.py` (le domande SUI documenti, anche su file strani o con ordini
  all'assistente, sono sul caso: senza regole su parole come «sospetto» il modello le riconosce dal prompt).
- **Quanto è severo il riscontro degli indizi**: `SOGLIA_RISCONTRO` in `backend/testo.py`.
- **Quanti fili sulla bacheca**: `FILI_PER_VOCE`, `FILO_MINIMO` in `backend/server.py`.
- **Navigazione**: le schede si aprono nella stessa finestra (`vaiA` in `comune.js`, `apri` in `app.js`); le frecce in alto
  a sinistra (`frecceNavigazione`) usano la cronologia. Le risposte visibili in chat si ricordano in `sessionStorage`
  e si ridisegnano tornando indietro (`ripristinaVista`). Mentre il modello scrive, le schede si aprono in una nuova scheda.
- **Memoria della chat**: le risposte sul caso non hanno memoria. "Cosa ti ho appena chiesto?", "e prima ancora?",
  "le ultime N domande" si rispondono dalla cronologia senza modello (`domanda_sulla_chat` in `motore.py`); le altre
  domande di chat ricevono gli scambi visibili numerati, e i riassunti anche gli elementi verificati
  (`riepilogo_verificato` in `server.py`). La cronologia è quella visibile in chat (`vista` mandata da `app.js`).
- **Domande di seguito** («e lui?», «e dopo?», «perché lo accusavano?»): il modello della risposta non riceve la conversazione, quindi un
  rimando senza il nome a cui si riferisce non troverebbe i documenti (e il controllo di pertinenza potrebbe scambiarlo per una chiacchiera).
  `motore.riscrivi_domanda` li rende autonomi con una chiamata breve (≈0,4-0,8 s) usando gli ultimi `RISCRIVI_TURNI` scambi visibili: parte
  solo se la domanda sembra un seguito (`sembra_seguito`: breve, aperta da una congiunzione o con un rimando; solo classi chiuse
  dell'italiano) e c'è un modello. La domanda riscritta serve a pertinenza e prompt (`domanda_ricerca` in `server.api_chiedi`); nello storico
  resta quella scritta dall'utente. La ricerca usa la domanda SCRITTA come principale e quella riscritta come classifiche in più con peso 0,7
  (`cerca_riscritta`, `PESI_RRF`): una riscrittura che aggiunge dettagli sbagliati («questi avvenimenti» → «la diffusione di informazioni non
  verificate sul Ripetitore Rame») non deve far perdere le parole dell'utente (persona, deposito, settore, pratica). Il riscrittore cambia il minimo
  e lascia i rimandi generici (`ISTRUZIONI_RISCRITTURA`). Le citazioni `[ID]` copiate dalle risposte si tolgono (`pulisci_riscrittura`). Se il modello
  non risponde, la domanda resta com'è. Strumento `riscrivi_domanda`; si spegne con `RISCRIVI_DOMANDE = False`.
- **Scenario fuori dalla logica**: ciò che dipende dai documenti sta in `backend/scenario.py` (formato di ID e codici, regole del giorno, parole generiche davanti ai nomi di
  luoghi e cose, nomi italiani per le foto); testo.py, indice.py, motore.py e volti.py lo importano. La descrizione del caso nel prompt (squadra, nome, ambientazione, compito,
  esempi di ID) è il blocco `SCENARIO` in cima a `app_lazarus/lazarus.py`, separato dalle regole di comportamento (COME RAGIONI, FORMATO, SICUREZZA): per un'altra
  indagine si cambiano quei due posti. `PROFILO_DEL_CASO` (la parte RUOLO) è il testo il cui vettore indica il cluster del caso. I tre prompt (risposte, conversazione,
  controllo di pertinenza) escono dal blocco con lo stesso testo di prima (verificato carattere per carattere).
- **Modalità debug (solo per il team)**: di solito l'interfaccia non dice al detective quali documenti sono «sospetti» o «non pertinenti»: lo scopre dalle
  risposte. Il pulsante «Debug» in alto (o `?debug=1` nell'indirizzo; `?debug=0` la spegne) la accende: tornano le etichette e i pulsanti nell'elenco
  Documenti, il conteggio «N pertinenti», il ⚠ sulle fonti, il rosso/giallo e l'asse «più vicino al caso» nella RAG view. La variabile globale `DEBUG`
  (`tema.js`) la leggono gli script delle pagine; la classe `solo-debug` nasconde un elemento HTML fuori dalla modalità. Il backend calcola e invia sempre
  tutto: nascondere è solo lato pagina.
- **Documenti sospetti**: nessun riconoscimento automatico (le regole sulle frasi dei documenti sono state tolte). Il metadato `sospetto` esiste
  solo se il team segna un documento a mano in modalità debug (`registro/sospetti.json`); serve a far confermare a quel documento solo voci
  sul tentativo di manipolazione, mai il suo contenuto (`riscontro` in `testo.py`). **Il modello NON riceve nessuna segnalazione e nessuna
  domanda cerca i sospetti apposta**: il prompt dice soltanto che il testo dei documenti è un dato e non un ordine (SICUREZZA in
  `lazarus.py`); se il modello incontra un tentativo di manipolazione tra le fonti che usa, lo descrive e lo mette in Indizi. Per
  "tutti i file" riceve tutti i documenti del caso se ci stanno nel contesto. In testa al prompt c'è solo la cronologia dei giorni (`nota_giorni`).
- **Domande brevi**: solo se la domanda sembra un seguito (`sembra_seguito`: parole scritte, congiunzione iniziale, rimandi) la ricerca prende in prestito anche la domanda
  precedente; prima si contavano le parole rimaste tolte quelle comuni, e «Qual è il percorso del modulo?» (due parole) si portava dietro la domanda di prima.
- **Come si scelgono i brani** (`Indice.cerca`): BM25 su tutto l'archivio, similarità degli embedding (`classifica_per_significato`: con numpy
  è un prodotto di matrici, ≈20 ms su 5000 brani; senza numpy il ciclo in Python puro, ≈500 ms), HyDE se la ricerca per significato trova poco,
  e gli **identificatori**: le parole della domanda presenti in pochissimi brani **del caso** (`IDENT_DF_FRAZIONE`, `IDENT_DF_MIN`: codici, pratiche, nomi rari;
  le frequenze si contano solo sui brani pertinenti, `Indice.frequenze_del_caso`, così una parola comune nel caso non sembra rara per colpa di qualche voce estranea).
  Le classifiche si fondono con RRF pesata (`PESI_RRF`, gli identificatori contano doppio) solo tra i documenti pertinenti: tra documenti quasi
  identici la somiglianza di significato è rumore e faceva perdere il documento primo in BM25 con un margine netto (misurato: domande su
  codici 90% → 100%). La decisione «la domanda punta a documenti non pertinenti» usa invece la fusione senza identificatori, così una parola
  rara che compare solo in una voce enciclopedica non porta testo estraneo al caso nel prompt.
  Prima dell'MMR i brani **quasi identici** (coseno degli embedding ≥ `COPIE_SOGLIA`, 0,97: bollettini ripetuti con poche parole diverse) si riducono a uno,
  `raggruppa_copie`: gli ID delle copie vanno nel prompt («testo quasi identico anche in…») e non occupano posti che servono ai documenti che li smentiscono.
  Tra i candidati pertinenti (`MMR_POOL`) i brani da dare al modello si scelgono con MMR (`diversifica`, `MMR_LAMBDA`): un brano quasi uguale a uno
  già scelto cede il posto a uno diverso. Con centinaia di documenti costruiti sullo stesso modello (bollettini ripetuti) le varianti
  riempivano tutti i posti e il documento con l'esito (un collaudo) restava fuori.
- **Ricerca a salti** (`motore.segui_riferimenti`, dopo la ricerca): dai 3 brani migliori si prendono i codici (pratiche, segnali, bollettini:
  `EV-441`, `B-417`, `M462`) e i nomi RARI di persona o di luogo (`entita_in`, che usa il riconoscimento di `testo.py` e toglie la parola generica:
  «deposito Bastione Cavo» → «Bastione Cavo»; un nome che compare in più di `ENTITA_MAX_BRANI` (12) brani del caso è un nodo comune, come «Ripetitore
  Rame», e non si segue). Si aggiungono, fino a `MAX_BRANI_AGGIUNTI` (6), i brani che li contengono come parola intera; un secondo salto parte
  dai brani aggiunti (es. «Bastione Cavo» → il documento con la pratica RV628 → il documento di Luca Serra). I legami scartati non consumano il limite. Con gli embedding, tra i brani che contengono il codice si scelgono quelli pertinenti alla domanda e DIVERSI da quelli già tra
  le fonti (stesso MMR di sopra, `diversifica(..., gia=fonti)`): un codice comune a centinaia di bollettini porta il documento che dice qualcosa di
  nuovo, non altre varianti. Senza embedding si torna al comportamento di prima (primi brani in ordine di archivio; codice già presente in più
  di 2 fonti = non si segue). Questo risolve le domande il cui documento non ha le parole della domanda («da dove è stato mandato il segnale?» →
  il collaudo che dice «invio riuscito»): il legame è il codice, non il vocabolario. Limite noto: senza codici in comune tra fonti e documento
  cercato il salto non parte.
- **Embedding**: la cache (`registro/cache_embedding.json`) si salva ogni `SALVA_OGNI` gruppi di brani e anche dopo un errore di Ollama, quindi
  un'interruzione non butta il lavoro fatto. I brani già in cache sono utilizzabili appena caricati i documenti. Con embedding incompleti
  (`embedding_ok` falso, `embedding_usabile` vero) la ricerca per significato lavora sui brani pronti e si fonde con le parole chiave; il
  clustering di pertinenza richiede tutti gli embedding (altrimenti tutti i documenti restano pertinenti).
- **Lunghezza della risposta**: `MAX_TOKEN` in `lazarus.py` è 600 (era 400: con Eventi/Indizi/Ipotesi gemma4:12b arriva spesso a 400 e la risposta si
  troncava a metà frase). Se una risposta tocca comunque il limite, lo strumento `genera_risposta` lo segnala con «TRONCATA dal limite di token».
- **Misure e prove della ricerca** (`misure/`, non fanno parte dell'app): `misura_ricerca.py` indicizza i documenti e calcola quante
  domande trovano i documenti attesi, in che posizione e in quanto tempo, senza chiamare il modello della risposta (`--modello` serve solo a
  misurare i seguiti con e senza riscrittura; `--imposta NOME=VALORE` prova un parametro; `--json` salva il risultato per confrontarlo).
  Domande: `domande_ricerca.json` (a mano, anche catene di documenti), `domande_seguiti.json`, e domande generate dai codici dei documenti.
  `test_ricerca.py` sono prove automatiche con embedding finti (nessun Ollama). `prima.json` è la misura prima di queste modifiche.
- **Tool SQL (giorno 4, `backend/sql.py`, `backend/missioni.py`)**: i file `.sqlite`/`.db` caricati sono database, non
  testo (`Indice.database`, anche più di uno). I CSV che esportano una tabella di un database della stessa cartella
  (`TABELLE_CSV/r03.csv`) non diventano documenti (`copia_di_tabella` in `indice.py`).
  - **Quale database**: `scegli_database` sceglie per parole della missione, valori e tabelle nominati ("Cocito", "V-46",
    "r08" -> archivio recuperato; "deposito", "LUME" -> accessi). Ci passano le domande su codici, permessi, accessi, e
    tutte quelle che nominano qualcosa di una missione.
  - **Missioni** (`missioni.json`, una voce per database): parametri (istante, varco, canale del veicolo...), regole,
    ipotesi sulle tabelle, piano a passi, tipo di verdetto. La centrale aggiorna i parametri dalla chat ("la centrale
    comunica che il canale del veicolo è Zeta", "nuovo istante 2026-11-04 12:30") o dalla pagina Schema logico: si salvano
    in `registro/missioni_parametri.json` con la storia (prima -> dopo). Non sono scritture sul database.
  - **Profilo** (`profila`): su tutte le righe, conteggi, distinti, NULL, intervalli di date, etichette, campioni e
    relazioni. Senza vincoli (archivio recuperato: `r03.k0`, `m12.k1`...) le relazioni si DEDUCONO: una colonna intera
    i cui valori sono tutti chiavi primarie di un'altra tabella la collega a quella (copertura 100%). È la "mappa con le
    prove" e lo schema che riceve il modello.
  - **Tre modi** (`agente_sql`): mappa (il profilo, senza query); piano a passi per le domande risolutive della missione
    (terminali, permessi, persone, finestre e chiavi: una query per passo, ognuna vede i passi prima); una query per il
    resto (esplorazione con conteggi e distinti, verifiche, codici).
  - **Controlli prima di eseguire** (`controlla_query`): JOIN incoerenti con le relazioni (una revoca collegata alla
    squadra invece che al permesso) e `ORDER BY ... LIMIT 1` per scegliere vengono rimandati al modello. Poi
    `interroga_database` in sola lettura (authorizer: solo SELECT sulle tabelle, funzioni limitate, tempo massimo).
    Errore, 0 righe o elenco in blocco senza filtri (più di 10 righe): il modello corregge, 3 tentativi.
  - **Esiti calcolati dal codice** (`annota`): colonna `esito` su ogni riga. Intervalli `X_inizio/X_fine` con inizio
    incluso, fine esclusa e fine NULL = senza scadenza; sospensioni e revoche escludono quando sono attive o efficaci
    (`X_effetto <= istante`); stati non attivi/operativi escludono; canale diverso da quello del veicolo esclude.
    `sintesi_varco` conclude: coppie SANITARIO + SICUREZZA della stessa squadra, terminali con finestra e chiave valide,
    esclusi con il motivo; più possibilità o un parametro mancante = non si sceglie, si chiede alla centrale.
    Verificato contro un calcolo indipendente in Python sullo stesso database: stessi risultati.
  - **Risposta**: codice con [DB], dossier con i loro ID; sotto la risposta il riquadro con query, passi ed esiti;
    "mostrami la query" la ripete. Ogni interrogazione va in `registro/missione_log.jsonl` con i parametri in vigore;
    ripetendo la stessa domanda il registro dice se il risultato è cambiato e quali parametri sono diversi (pagina
    Schema logico, "Ripeti test"). Richieste di cambiare i dati: rifiutate (`chiede_di_scrivere`: un verbo al participio, «chi ha
    aggiornato lo stato?», è una domanda sui dati, non una scrittura).
  - **Seguiti**: lo smistamento usa la domanda riscritta (`domanda_ricerca`); un seguito («e il suo codice?») di una risposta passata dal
    tool torna allo stesso database.
  - **Tempi**: i passi del piano arrivano alla pagina appena finiti (`avanzamento` in `agente_sql`, evento `strumento` con `passo`).
    Per la mappa i documenti non si cercano; per il piano a passi si cercano senza HyDE (alla risposta ne vanno solo 2 brani). Le query
    le scrive sempre il modello: nessun riuso di query o risultati.
  - **Giorno di un database**: dalle cartelle (`GIORNATA 4`, `Imprevisti_g_4`), altrimenti dal nome del file (`accessi_g_4.sqlite`).
  - **CSV senza il loro database** (`TABELLE_CSV` caricata da sola): due o più CSV-tabella nella stessa cartella diventano un database
    (`Indice._database_da_csv` -> `sql.database_da_csv`, in `registro/database_da_csv/`, ricostruito solo se i CSV cambiano) con il nome
    della missione che ha quelle tabelle (`missioni.missione_per_tabelle`: TABELLE_CSV -> `archivio_recuperato.sqlite`). Tipi come
    nell'originale: interi per le chiavi (k0, k1, id) e i numeri di lunghezza variabile, testo per i codici a lunghezza fissa e con zeri
    iniziali; vuoto = NULL. Verificato sull'archivio del giorno 4: stessi tipi, stesse 16 relazioni, stessi dati riga per riga. Prima i
    CSV erano letti come testo: due righe vicine di r03.csv diventavano «le due persone autorizzate». Un CSV da solo resta un documento.
  - **Tentativi del modello** (`_una_query`): 0 righe perché un valore della query non esiste nel database (`valori_assenti`, «PX9999») è la
    risposta: niente altri tentativi, che spingevano il modello a cambiare filtro fino a trovare un altro codice; il prompt della
    risposta riceve l'esito («nel database non esiste…»). Le query rifiutate dicono al modello il perché (tabella non leggibile, funzione
    non permessa con l'elenco di quelle ammesse, solo SELECT) invece del solo «not authorized».
  - **Cache**: schema (`descrivi`) e profilo si ricordano per database finché il file non cambia (prima lo schema si rileggeva a ogni query,
    il profilo solo per l'ultimo database usato).
  - **Domande risolutive**: `risolutiva` in `missioni.json` riconosce anche «due persone che possono accedere all'archivio Cocito», «chi può
    entrare»; `missioni._norma` toglie gli accenti («può» = «puo»).
- **Quanti documenti vede il modello**: se i documenti pertinenti stanno in `SOGLIA_CARATTERI` e `SOGLIA_BRANI` (non sono numeri fissi: un terzo di `NUM_CTX` per i documenti,
  `FRAZIONE_CONTESTO_DOCUMENTI` e `CARATTERI_PER_TOKEN` in `config.py`; con NUM_CTX 12288 fanno 12288 caratteri e 40 brani)
  (40) li riceve tutti, altrimenti i `TOP_K` (10) brani migliori; mai più di `MAX_BRANI_PER_DOC` (2) brani per documento
  (`limita_per_documento` in `indice.py`). Contesto del modello `NUM_CTX` = 12288 in `lazarus.py`: fisso, così Ollama
  non ricarica il modello quando il prompt cresce (prima parola ~3 s con ~4500 token di prompt).
- **Tempo dell'indagine**: ogni documento ha il metadato `giorno` (dal nome delle cartelle `…giorno_N…`, altrimenti
  "giorno operativo NN" nel testo o l'ID `G2-…`; `giorno_di` in `indice.py`). Le fonti arrivano al modello in ordine di
  giorno e con "(giorno N)"; il prompt contiene la CRONOLOGIA DELL'INDAGINE (`nota_giorni` in `motore.py`); le domande
  su uno o più giorni ("giorno 2", "dal primo al terzo", "fino al giorno 2 compreso", "i primi due giorni") ricevono i documenti di quei giorni
  (`giorni_chiesti`; «fino a»/«entro» e «i primi N giorni» partono dal giorno 1).
- **Velocità di caricamento e rimozione**: l'elenco delle persone si calcola dopo il clustering e solo sui pertinenti
  (prima si scansionavano anche le 1000 voci estranee: ~17 s a ogni modifica, ora ~4 s in tutto).
- **RAG view** (`frontend/ragview.html`, pulsante sotto "Accesso Segreto"): mappa dei documenti (x = somiglianza al caso,
  y e z = le due variazioni principali rimanenti; mappa 3D ruotabile, `_mappa_3d` in `pertinenza.py`), tabella dei cluster, e il percorso di una domanda:
  classificazione, classifiche BM25 / embedding / HyDE, fusione RRF con l'esito di ogni brano, fonti e prompt esatto.
  API: `GET /api/ragview/archivio`, `GET /api/ragview/domanda?q=` (non genera la risposta e non scrive nello storico;
  le classifiche arrivano da `Indice.cerca(..., traccia=)`).
- **Pertinenza (clustering)**: quali documenti riguardano l'indagine lo decide il contenuto, non l'origine del file
  (`pertinenza.py`, chiamato da `Indice.calcola_pertinenza` dopo gli embedding). Vettore medio per documento, centratura
  sulla media dell'archivio, k-means (K ≈ √(N/2), k-means++ con 4 ripartenze); livello 1: cluster vicini al profilo del
  caso (Otsu); livello 2: si tolgono i documenti più vicini agli estranei, poi rientra chi ha la maggioranza dei 5 vicini
  nel dominio; livello 3: coerenza di cartella (≥60% dell'indagine). Regge anche i 500 registri di routine del giorno 3
  (stesso stile del caso): 15/15 documenti G/ARCH presi, 0 voci enciclopediche. Sotto 40 documenti sono tutti pertinenti. Sull'archivio del giorno 3:
  24/24 documenti del caso presi, 1 intruso su 1000. Sui 1523 documenti dei giorni 1-3 (con le voci enciclopediche): 523 pertinenti, 518/518 del caso,
  5 intrusi (la rifinitura a due gruppi usata prima su hotfix/interfaccia-senza-segnali dava 526 e 8). Le domande sul caso ricevono solo documenti pertinenti; se la
  domanda riguarda file non pertinenti (es. "parlami dell'equatore") il modello ne riceve il testo segnato come non
  pertinente e ne parla, ma non diventano mai indizi, eventi o luoghi (`cerca`, `del_caso`). numpy lo rende istantaneo (`--with numpy` nel .bat), senza ~1 minuto.
- **Non pertinenti (campo `consultazione`)**: le voci enciclopediche sono segnalate al modello e non diventano mai indizi, eventi o luoghi
  (`del_caso` in `server.py`). Il diario delle risposte (`diario_chat.jsonl`) non entra nell'indice.
- **Documenti uguali, risposte uguali**: i fine riga si uniformano alla lettura (`decodifica`) e i documenti si ordinano
  per nome (`ordine_naturale`), così lo stesso file dà lo stesso prompt da qualunque cartella o sistema arrivi.
- **Scelta dei brani (RAG)**: `Indice.cerca` fonde con RRF pesata quattro classifiche: parole chiave (BM25), significato (embedding),
  identificatori (parole rare della domanda, vedi «Come si scelgono i brani») e, solo se la migliore similarità è sotto `HYDE_SOGLIA`,
  HyDE (il modello scrive una risposta ipotetica, `risposta_ipotetica` in `modello.py`, e si cercano i brani simili a quella).
  Interruttori in `config.py`: `HYDE = False` torna alla ricerca senza HyDE; `PESI_RRF['identificatori'] = 0` senza identificatori.
- **Citazioni**: `[G1-02]`, `[ARCH-6408]` o `[LX4-7783]` diventano link se corrispondono al nome del file o all'ID scritto
  nel documento (`id_interno` in `backend/indice.py`, `documentoCitato` in `app.js`), senza badare a trattini e maiuscole.
- **Colori e temi**: solo le variabili in cima a `frontend/app.css`. Colori delle puntine: `--p-indizio`, `--p-ipotesi`, `--p-evento`, `--p-persona`, `--p-luogo` in `frontend/bacheca.html`.
- **Aggiungere un volto**: metti l'immagine in `frontend/volti/` e aggiungi una riga in `frontend/volti/volti.json` con `sesso` (`f`/`m`),
  misure dell'immagine e riquadro quadrato del viso (`x`, `y` del centro e `lato`, in pixel). Le persone già assegnate tengono la loro foto;
  le nuove ricevono la foto meno usata del loro sesso. Per riassegnare tutto da capo cancella `registro/volti.json`.
- **Nomi maschili in -a / femminili non in -a**: elenchi `MASCHILI_IN_A` e `FEMMINILI` in `backend/volti.py`.
- **Persona o luogo?**: `PREFISSI_LUOGO` e `PREFISSI_SANTI` in `backend/testo.py`. Un nome che inizia con una di queste
  parole (laboratorio, deposito, ripetitore, antenna, Santa, San…) è un luogo e non può essere una persona.
  `PREFISSI_COSA` fa lo stesso per apparecchi, mezzi, gruppi e documenti (generatore, veicolo, squadra…): non sono
  persone né luoghi. Entrambi contano solo come prima parola del nome, così un cognome come "Canale" resta valido.
  Scritte in minuscolo subito prima di un nome lo rendono il nome di quella cosa ("il deposito Darsena Vuota",
  "i ripetitori Rame e Fosso"). I documenti con `source: https://…` (le voci di Wikipedia dell'archivio) sono di
  non pertinenti per il clustering: non entrano nell'elenco delle persone.
- **Ogni chiamata a Ollama** passa da `prepara_richiesta` (`lazarus.py`, usata anche da `backend/modello.py`): mette un codice
  diverso in testa al prompt, così Ollama non riusa lo stato di una domanda precedente (con gemma a volte lo riprendeva
  sbagliato e il modello perdeva il ruolo), e allarga il contesto se istruzioni + documenti + risposta non ci stanno.
  Le nuove chiamate al modello devono passare da qui.
- **Modello usato**: `MODELLO` in `avvia_interfaccia.bat`.
- **Documenti esclusi per errore dal clustering** (`RECUPERA_CON_CODICI` in `config.py`): un codice raro (in pochi brani) della domanda o di un brano
  già trovato porta anche ai brani esclusi che lo contengono (`Indice.indice_codici`). Per quella risposta valgono come caso (`recuperato` nella fonte,
  segnalato negli strumenti), ma non diventano indizi: l'esclusione nell'indice resta.
- **Documenti lunghi**: i salti, le domande sui giorni e «tutti i file» usano TUTTI i brani del caso (prima solo i primi `MAX_BRANI_PER_DOC` di ogni
  documento, in ordine di archivio). «Tutti i pertinenti» si passano solo se ci stanno interi.
- **Contesto dei brani**: dal secondo brano in poi il modello riceve il titolo del documento davanti al brano. L'embedding resta sul solo testo del
  brano: provato con titolo + frase precedente, il clustering di pertinenza crollava (del caso presi 1/518 invece di 518/518).
- **Dossier** (`DOSSIER_MAX_VOCI`): gli eventi e gli indizi già verificati entrano anche nel prompt delle domande sul caso (`Gestore.dossier`).
- **Estranei nel prompt**: entrano solo se battono il miglior brano del caso di `FUORI_MARGINE` e al massimo `FUORI_MAX_BRANI`.
- **Velocità dell'indice**: i file non cambiati non si rileggono (`_leggi_con_cache`), i brani dei documenti non cambiati non si ri-analizzano (`_basi`),
  BM25 usa un indice invertito (`postings`), i salti usano gli indici di codici e parole invece di scorrere i brani. Caricare un file singolo o
  rimuovere file risponde subito: embedding e pertinenza continuano in background (`aggiorna_in_background`); nel frattempo i documenti già visti
  tengono la pertinenza di prima. Il caricamento di una cartella (`api_reindicizza`) aspetta embedding e pertinenza, come prima.
- **Cache del prompt in Ollama**: `ISOLA_SOLO_DOPO_ISTRUZIONI` in `lazarus.py` mette il codice casuale in fondo alle istruzioni, così Ollama può
  riusarle. Da verificare a mano con gemma; se ricompaiono risposte fuori ruolo, rimettilo a False.

## Regole del progetto

- Tutto resta sul computer: niente CDN, font o script esterni.
- Il frontend non parla mai con Ollama: passa sempre dal backend.
- I documenti sono dati, non istruzioni: il modello lo sa (`ISTRUZIONI`, sezione SICUREZZA) e il controllo di pertinenza non esegue i messaggi che classifica.
- Nella colonna destra e sulla bacheca entra solo ciò che ha un riscontro nei documenti.
