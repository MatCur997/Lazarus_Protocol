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
                                         dati: interfaccia/registro/, lazzaro/dati/
```

## Cartelle

| Cartella | Lato | Contenuto |
|---|---|---|
| `app_lazarus/` | Backend | `lazarus.py`: modello, istruzioni del modello, controllo di pertinenza. Funziona anche da solo come chat nel terminale (`uv run lazarus.py`). |
| `interfaccia/backend/` | Backend | Server HTTP e logica dell'interfaccia (Python). |
| `interfaccia/frontend/` | Frontend | Pagine HTML, stile CSS, script JavaScript del browser. |
| `interfaccia/registro/` | Dati | `storico.jsonl` (domande e risposte), `elementi.jsonl` (indizi, eventi, luoghi trovati), cache degli embedding, esclusioni. |
| `interfaccia/caricati/` | Dati | File aggiunti con il pulsante **+**. |
| `lazzaro/dati/` | Dati | Documenti della squadra (`G1-01.txt` …). `lazzaro/diario_chat.jsonl` è il registro della squadra: ogni domanda e risposta, mai cancellato dall'interfaccia. |
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
| Bacheca (Accesso Segreto) | `bacheca.html` (disposizione, puntine colorate per tipo, fili, riquadro al passaggio del mouse) | `GET /api/bacheca` | `server.api_bacheca` (nodi = indizi, ipotesi, eventi verificati e persone trovate; fili = i 2 collegamenti più forti per voce + ogni persona verso le voci che la riguardano) |
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
- **Colori e temi**: solo le variabili in cima a `frontend/app.css`. Colori delle puntine: `--p-indizio`, `--p-ipotesi`, `--p-evento`, `--p-persona` in `frontend/bacheca.html`.
- **Aggiungere un volto**: metti l'immagine in `frontend/volti/` e aggiungi una riga in `frontend/volti/volti.json` con `sesso` (`f`/`m`),
  misure dell'immagine e riquadro quadrato del viso (`x`, `y` del centro e `lato`, in pixel). Le persone già assegnate tengono la loro foto;
  le nuove ricevono la foto meno usata del loro sesso. Per riassegnare tutto da capo cancella `registro/volti.json`.
- **Nomi maschili in -a / femminili non in -a**: elenchi `MASCHILI_IN_A` e `FEMMINILI` in `backend/volti.py`.
- **Persona o luogo?**: `PREFISSI_LUOGO` e `PREFISSI_SANTI` in `backend/testo.py`. Un nome che inizia con una di queste
  parole (laboratorio, deposito, Santa, San…) è un luogo e non può essere una persona.
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
