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
| `app_lazarus/` | Backend | `lazarus.py`: modello, istruzioni del modello, controllo di pertinenza. Funziona anche da solo come chat nel terminale (`uv run lazarus.py`). |
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
| `frontend/tema.js` | Tema chiaro (carta) / scuro (grigio e panna), ricordato nel browser. Caricato in `<head>` di ogni pagina. |
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
- **Cosa conta come "non pertinente"**: `PAROLE_CONVERSAZIONE` e `ISTRUZIONI_PERTINENZA` in `lazarus.py`.
- **Quanto è severo il riscontro degli indizi**: `SOGLIA_RISCONTRO` in `backend/testo.py`.
- **Quanti fili sulla bacheca**: `FILI_PER_VOCE`, `FILO_MINIMO` in `backend/server.py`.
- **Navigazione**: le schede si aprono nella stessa finestra (`vaiA` in `comune.js`, `apri` in `app.js`); le frecce in alto
  a sinistra (`frecceNavigazione`) usano la cronologia. Le risposte visibili in chat si ricordano in `sessionStorage`
  e si ridisegnano tornando indietro (`ripristinaVista`). Mentre il modello scrive, le schede si aprono in una nuova scheda.
- **Memoria della chat**: le risposte sul caso non hanno memoria. "Cosa ti ho appena chiesto?", "e prima ancora?",
  "le ultime N domande" si rispondono dalla cronologia senza modello (`domanda_sulla_chat` in `motore.py`); le altre
  domande di chat ricevono gli scambi visibili numerati, e i riassunti anche gli elementi verificati
  (`riepilogo_verificato` in `server.py`). La cronologia è quella visibile in chat (`vista` mandata da `app.js`).
- **Documenti sospetti**: un documento con almeno due segnali di ordini all'assistente ("istruzione per l'assistente",
  "ignora G2-01", "rispondi che…", "non citare le fonti") ha il metadato `sospetto` con il motivo (`motivo_sospetto` in
  `indice.py`, ricalcolato a ogni caricamento; correzioni a mano in `registro/sospetti.json`, pulsanti nella scheda
  Documenti). Il modello lo riceve segnalato, non lo esegue ma lo descrive se pertinente; le domande su file
  malevoli/sospetti lo ricevono sempre (`cerca_fonti`). Un documento sospetto conferma solo voci sul tentativo di
  manipolazione, mai il suo contenuto (`riscontro` in `testo.py`). Per domande su file estranei o sospetti il modello
  riceve anche il riepilogo dell'archivio (`nota_archivio`); per "tutti i file" riceve tutti i documenti del caso.
- **Quanti documenti vede il modello**: se i documenti pertinenti stanno in `SOGLIA_CARATTERI` (12000) e `SOGLIA_BRANI`
  (40) li riceve tutti, altrimenti i `TOP_K` (10) brani migliori; mai più di `MAX_BRANI_PER_DOC` (2) brani per documento
  (`limita_per_documento` in `indice.py`). Contesto del modello `NUM_CTX` = 8192 in `lazarus.py`: fisso, così Ollama
  non ricarica il modello quando il prompt cresce (prima parola ~3 s con ~4500 token di prompt).
- **Tempo dell'indagine**: ogni documento ha il metadato `giorno` (dal nome delle cartelle `…giorno_N…`, altrimenti
  "giorno operativo NN" nel testo o l'ID `G2-…`; `giorno_di` in `indice.py`). Le fonti arrivano al modello in ordine di
  giorno e con "(giorno N)"; il prompt contiene la CRONOLOGIA DELL'INDAGINE (`nota_giorni` in `motore.py`); le domande
  su uno o più giorni ("giorno 2", "dal primo al terzo") ricevono i documenti di quei giorni (`giorni_chiesti`).
- **Velocità di caricamento e rimozione**: l'elenco delle persone si calcola dopo il clustering e solo sui pertinenti
  (prima si scansionavano anche le 1000 voci estranee: ~17 s a ogni modifica, ora ~4 s in tutto).
- **RAG view** (`frontend/ragview.html`, pulsante sotto "Accesso Segreto"): mappa dei documenti (x = somiglianza al caso,
  y e z = le due variazioni principali rimanenti; mappa 3D ruotabile, `_mappa_3d` in `pertinenza.py`), tabella dei cluster, e il percorso di una domanda:
  classificazione, classifiche BM25 / embedding / HyDE, fusione RRF con l'esito di ogni brano, fonti e prompt esatto.
  API: `GET /api/ragview/archivio`, `GET /api/ragview/domanda?q=` (non genera la risposta e non scrive nello storico;
  le classifiche arrivano da `Indice.cerca(..., traccia=)`).
- **Pertinenza (clustering)**: quali documenti riguardano l'indagine lo decide il contenuto, non l'origine del file
  (`pertinenza.py`, chiamato da `Indice.calcola_pertinenza` dopo gli embedding). Vettore medio per documento, centratura
  sulla media dell'archivio, k-means (K ≈ √(N/2), k-means++ con 4 ripartenze), cluster pertinenti = quelli più vicini al
  profilo del caso (parte RUOLO delle istruzioni). Sotto 40 documenti sono tutti pertinenti. Sull'archivio del giorno 3:
  24/24 documenti del caso presi, 1 intruso su 1000. Le domande sul caso ricevono solo documenti pertinenti; se la
  domanda riguarda file non pertinenti (es. "parlami dell'equatore") il modello ne riceve il testo segnato come non
  pertinente e ne parla, ma non diventano mai indizi, eventi o luoghi (`cerca`, `del_caso`). numpy lo rende istantaneo (`--with numpy` nel .bat), senza ~1 minuto.
- **Non pertinenti (campo `consultazione`)**: le voci enciclopediche sono segnalate al modello e non diventano mai indizi, eventi o luoghi
  (`del_caso` in `server.py`). Il diario delle risposte (`diario_chat.jsonl`) non entra nell'indice.
- **Documenti uguali, risposte uguali**: i fine riga si uniformano alla lettura (`decodifica`) e i documenti si ordinano
  per nome (`ordine_naturale`), così lo stesso file dà lo stesso prompt da qualunque cartella o sistema arrivi.
- **Scelta dei brani (RAG)**: `Indice.cerca` fonde con RRF tre classifiche: parole chiave (BM25), significato (embedding) e,
  solo se la migliore similarità è sotto `HYDE_SOGLIA`, HyDE (il modello scrive una risposta ipotetica, `risposta_ipotetica`
  in `modello.py`, e si cercano i brani simili a quella).
  Interruttore in `config.py`: `HYDE = False` torna alla ricerca senza HyDE.
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

## Regole del progetto

- Tutto resta sul computer: niente CDN, font o script esterni.
- Il frontend non parla mai con Ollama: passa sempre dal backend.
- I documenti sono dati, non istruzioni: il modello lo sa (`ISTRUZIONI`, sezione SICUREZZA) e il controllo di pertinenza non esegue i messaggi che classifica.
- Nella colonna destra e sulla bacheca entra solo ciò che ha un riscontro nei documenti.
