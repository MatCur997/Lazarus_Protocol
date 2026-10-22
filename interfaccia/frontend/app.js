// Investigatore LAZZARO - interfaccia. Nessuna libreria esterna.
// Usa $, esc, markdown e senzaMarkdown da comune.js (caricato prima in index.html).
const sec = (ms) => (ms == null ? '—' : (ms / 1000).toLocaleString('it-IT', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ' s');
const media = (a) => (a.length ? a.reduce((x, y) => x + y, 0) / a.length : null);

const ICONE = {
  riesegui: '<svg viewBox="0 0 24 24"><path d="M21 12a9 9 0 1 1-3-6.7L21 8"/><path d="M21 3v5h-5"/></svg>',
  file: '<svg viewBox="0 0 24 24"><path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4"/></svg>',
  x: '<svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6 6 18"/></svg>',
  apri: '<svg viewBox="0 0 24 24"><path d="M14 4h6v6M20 4l-9 9M18 14v6H4V6h6"/></svg>',
  luoghi: '<svg viewBox="0 0 24 24"><path d="M12 21s-7-6.2-7-11.5a7 7 0 0 1 14 0C19 14.8 12 21 12 21z"/><circle cx="12" cy="9.5" r="2.5"/></svg>',
  indizi: '<svg viewBox="0 0 24 24"><circle cx="10.5" cy="10.5" r="6"/><path d="m15 15 5.5 5.5M8 10.5h5M10.5 8v5"/></svg>',
  eventi: '<svg viewBox="0 0 24 24"><path d="M13 2 4 14h7l-1 8 9-12h-7z"/></svg>',
};
const NOMI_TIPO = { persone: 'Persone', luoghi: 'Luoghi', indizi: 'Indizi', eventi: 'Eventi' };

const stato = {
  scheda: 'storico', storico: [], strumenti: [], documenti: [], server: {}, aperte: new Set(), occupato: false,
  elementi: { persone: [], luoghi: [], indizi: [], eventi: [] }, tipoDestra: 'persone',
};

// ------------------------------------------------------------------ API
async function api(percorso, opzioni) {
  const r = await fetch(percorso, opzioni);
  const dati = await r.json();
  if (!r.ok) throw new Error(dati.errore || r.statusText);
  return dati;
}
const postJson = (percorso, corpo) => api(percorso, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(corpo) });

async function aggiornaTutto() {
  const [server, storico, strumenti, documenti, elementi] = await Promise.all([
    api('/api/stato'), api('/api/storico'), api('/api/strumenti'), api('/api/documenti'), api('/api/elementi'),
  ]);
  Object.assign(stato, { server, storico, strumenti, documenti, elementi });
  disegnaStato();
  disegnaSinistra();
  disegnaElementi();
}

// ------------------------------------------------------------- testata
function disegnaStato() {
  const s = stato.server;
  const sel = $('#modello');
  const modelli = s.demo ? ['demo'] : Array.from(new Set([s.modello, ...(s.modelli || [])]));
  if (sel.dataset.elenco !== modelli.join('|')) {
    sel.innerHTML = modelli.map((m) => `<option ${m === s.modello ? 'selected' : ''}>${esc(m)}</option>`).join('');
    sel.dataset.elenco = modelli.join('|');
  }
  sel.disabled = s.demo;
  const pill = (id, classe, testo) => { $(id).innerHTML = `<i class="punto ${classe}"></i>${esc(testo)}`; };
  pill('#stato-ollama', s.demo ? 'attesa' : s.ollama ? 'ok' : 'ko', s.demo ? 'Modalità demo' : s.ollama ? 'Ollama attivo' : 'Ollama spento');
  pill('#stato-embedding', s.embedding ? 'ok' : 'attesa', s.embedding ? `Ricerca ibrida · ${s.modello_embedding}` : 'Solo parole chiave');
  $('#stato-documenti').lastChild.textContent = `${s.documenti} documenti · ${s.brani} brani`;
  const ok = stato.storico.filter((v) => !v.errore);
  $('#stato-media').lastChild.textContent = `media ${sec(media(ok.map((v) => v.tempi.totale_ms)))} · prima parola ${sec(media(ok.map((v) => v.tempi.prima_parola_ms).filter((x) => x != null)))}`;
  const pronto = s.demo || s.ollama;
  $('#punto-chat').className = 'punto ' + (s.demo ? 'attesa' : !pronto ? 'ko' : s.avviso ? 'attesa' : 'ok');
  $('#sotto-chat').textContent = s.demo ? 'Modalità demo: risposte finte per provare l\'interfaccia'
    : !pronto ? 'Ollama non raggiungibile: avvialo e ricarica la pagina'
    : s.avviso ? `Attenzione: ${s.avviso}` : `${s.modello} · in ascolto · ${s.top_k} brani per domanda`;
}

$('#modello').addEventListener('change', async (e) => {
  await postJson('/api/modello', { modello: e.target.value });
  stato.server.modello = e.target.value;
  disegnaStato();
  sistema(`Modello cambiato in ${e.target.value}: lo carico in memoria.`);
});

$('#cerca-documenti').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && e.target.value.trim()) apri(`persona.html?q=${encodeURIComponent(e.target.value.trim())}`);
});

// ------------------------------------------------------ pannello sinistro
const TITOLI = { storico: 'Storico', strumenti: 'Strumenti del modello', tempi: 'Tempi di risposta', documenti: 'Documenti' };
document.querySelectorAll('.icona-scheda').forEach((b) => b.addEventListener('click', () => {
  document.querySelectorAll('.icona-scheda').forEach((x) => x.classList.toggle('attiva', x === b));
  stato.scheda = b.dataset.scheda;
  $('#filtro-sinistra').value = '';
  disegnaSinistra();
}));
$('#filtro-sinistra').addEventListener('input', disegnaSinistra);
$('#svuota-storico').addEventListener('click', async () => {
  if (!confirm(`Cancellare tutte le ${stato.storico.length} domande dello storico? Non si può annullare.`)) return;
  try { await postJson('/api/storico/svuota', {}); await aggiornaTutto(); } catch (err) { alert(err.message); }
});

// "Svuota caricati": nella scheda Documenti e, sempre visibile, il cestino accanto ai pulsanti di caricamento
async function svuotaCaricati() {
  const n = stato.documenti.filter((d) => d.origine === 'caricato').length;
  if (!n) return sistema('Non ci sono documenti caricati da togliere.');
  if (!confirm(`Togliere tutti i ${n} documenti caricati? I file vengono cancellati dalla cartella caricati. `
    + 'Non si può annullare.')) return;
  const bottoni = [$('#svuota-caricati'), $('#svuota-caricati-chat')];
  bottoni.forEach((b) => { b.disabled = true; });
  $('#svuota-caricati').textContent = 'svuoto…';
  sistema(`Tolgo ${n} documenti caricati e aggiorno l'indice…`);
  try {
    const r = await postJson('/api/caricati/svuota', {});
    $('#file-in-esame').innerHTML = '';
    sistema(`Caricati svuotati: ${r.tolti} file tolti, restano ${r.documenti} documenti (${r.ms} ms).`);
    await aggiornaTutto();
  } catch (err) {
    sistema('Errore nello svuotamento: ' + err.message);
  } finally {
    bottoni.forEach((b) => { b.disabled = false; });
    $('#svuota-caricati').textContent = 'Svuota caricati';
  }
}
$('#svuota-caricati').addEventListener('click', svuotaCaricati);
$('#svuota-caricati-chat').addEventListener('click', svuotaCaricati);

function disegnaSinistra() {
  $('#titolo-sinistra').textContent = TITOLI[stato.scheda];
  $('#svuota-storico').hidden = stato.scheda !== 'storico' || !stato.storico.length;
  const caricati = stato.documenti.filter((d) => d.origine === 'caricato').length;
  $('#svuota-caricati').hidden = stato.scheda !== 'documenti' || !caricati;
  $('#svuota-caricati-chat').hidden = !caricati;
  $('#svuota-caricati-chat').title = `Togli tutti i ${caricati} documenti caricati`;
  const filtro = $('#filtro-sinistra').value.trim().toLowerCase();
  const lista = $('#lista-sinistra');
  ({ storico: disegnaStorico, strumenti: disegnaStrumenti, tempi: disegnaTempi, documenti: disegnaDocumenti })[stato.scheda](lista, filtro);
}

function disegnaStorico(lista, filtro) {
  const voci = stato.storico.filter((v) => !filtro || (v.domanda + ' ' + v.risposta).toLowerCase().includes(filtro)).reverse();
  $('#conta-sinistra').textContent = `${stato.storico.length} domande`;
  lista.innerHTML = voci.length ? voci.map((v) => `
    <article class="voce ${stato.aperte.has(v.id) ? 'aperta' : ''}" data-id="${v.id}">
      <div class="testa"><span>${esc(v.ora)}</span><span>${esc(v.modello)}</span></div>
      <div class="domanda" data-apri>${esc(v.domanda)}</div>
      <div class="anteprima" data-apri>${v.errore ? `<span class="etichetta errore">errore</span> ${esc(v.errore)}` : stato.aperte.has(v.id) ? formatta(v.risposta) : esc(senzaMarkdown(v.risposta))}</div>
      ${stato.aperte.has(v.id) && v.fonti?.length ? `<div class="fila" style="margin-top:8px">${v.fonti.map((f) => `<span class="etichetta bordeaux">${esc(f.doc)}</span>`).join('')}</div>` : ''}
      <div class="piede">
        <span class="etichetta" title="tempo totale · prima parola">${sec(v.tempi.totale_ms)} · ${sec(v.tempi.prima_parola_ms)}</span>
        <span class="fila"><button class="btn-piccolo grigio" data-elimina title="Cancella questa domanda dallo storico">Elimina</button>
        <button class="btn-piccolo" data-riesegui title="Riesegui la domanda con il modello selezionato">${ICONE.riesegui}Riesegui</button></span>
      </div>
    </article>`).join('') : `<div class="vuoto">${filtro ? 'Nessun risultato.' : 'Ancora nessuna domanda.'}</div>`;
}

$('#lista-sinistra').addEventListener('click', (e) => {
  const voce = e.target.closest('.voce');
  if (!voce) return;
  const v = stato.storico.find((x) => x.id === voce.dataset.id);
  if (e.target.closest('[data-riesegui]')) return chiedi(v.domanda, v.id);
  if (e.target.closest('[data-elimina]')) {
    const breve = v.domanda.length > 80 ? `${v.domanda.slice(0, 80)}…` : v.domanda;
    if (!confirm(`Cancellare dallo storico la domanda «${breve}» e la sua risposta? Non si può annullare.`)) return;
    return postJson('/api/storico/elimina', { id: voce.dataset.id }).then(aggiornaTutto).catch((err) => alert(err.message));
  }
  if (e.target.closest('[data-rimuovi]')) return rimuoviDocumento(voce.dataset.id);
  const segna = e.target.closest('[data-sospetto]');
  if (segna) return segnaSospetto(voce.dataset.id, { true: true, false: false, auto: null }[segna.dataset.sospetto]);
  if (e.target.closest('[data-documento]')) return apri(`persona.html?doc=${encodeURIComponent(voce.dataset.id)}`);
  if (e.target.closest('[data-apri]') && v) {
    stato.aperte.has(v.id) ? stato.aperte.delete(v.id) : stato.aperte.add(v.id);
    disegnaSinistra();
  }
});

function disegnaStrumenti(lista, filtro) {
  const ultimo = stato.storico[stato.storico.length - 1];
  const usati = stato.strumenti.filter((s) => s.chiamate).length;
  $('#conta-sinistra').textContent = `${usati}/${stato.strumenti.length} usati`;
  const sequenza = ultimo ? `
    <div class="sequenza"><h3>Ultima domanda · ordine di chiamata</h3>
      ${ultimo.strumenti.map((s, i) => `<div class="passo" title="${esc(s.dettaglio)}"><span class="n">${i + 1}</span><code>${esc(s.nome)}</code><span class="etichetta">${s.ms} ms</span></div>`).join('')}
    </div>` : '';
  const voci = stato.strumenti.filter((s) => !filtro || (s.nome + s.descrizione).toLowerCase().includes(filtro));
  lista.innerHTML = sequenza + voci.map((s) => `
    <article class="voce strumento">
      <div class="nome"><span>${esc(s.nome)}</span>${s.attivo ? '' : '<span class="etichetta attenzione">non attivo</span>'}</div>
      <p>${esc(s.descrizione)}</p>
      <div class="numeri"><span class="etichetta bordeaux">${s.chiamate} chiamate</span><span class="etichetta">media ${s.ms_medio == null ? '—' : s.ms_medio + ' ms'}</span></div>
      ${s.ultimo ? `<div class="ultimo">Ultima: ${esc(s.ultimo.dettaglio)}</div>` : ''}
    </article>`).join('');
}

function disegnaTempi(lista, filtro) {
  const ok = stato.storico.filter((v) => !v.errore);
  $('#conta-sinistra').textContent = `${ok.length} risposte`;
  const tot = ok.map((v) => v.tempi.totale_ms);
  const pp = ok.map((v) => v.tempi.prima_parola_ms).filter((x) => x != null);
  const ultimo = ok[ok.length - 1];
  const perModello = {};
  ok.forEach((v) => (perModello[v.modello] ||= []).push(v));
  const righeModelli = Object.entries(perModello).map(([m, vv]) => {
    const ts = vv.map((v) => v.tempi.tok_s).filter(Boolean);
    return `<tr><td>${esc(m)}</td><td class="num">${vv.length}</td><td class="num">${sec(media(vv.map((v) => v.tempi.totale_ms)))}</td>
      <td class="num">${sec(media(vv.map((v) => v.tempi.prima_parola_ms).filter((x) => x != null)))}</td><td class="num">${ts.length ? media(ts).toFixed(0) : '—'}</td></tr>`;
  }).join('');
  const max = Math.max(1, ...tot);
  const voci = ok.filter((v) => !filtro || v.domanda.toLowerCase().includes(filtro) || v.modello.toLowerCase().includes(filtro)).reverse();
  lista.innerHTML = `
    <div class="kpi">
      <div class="evidenza"><b>${sec(media(tot))}</b><span>tempo medio di risposta</span></div>
      <div><b>${sec(media(pp))}</b><span>media alla prima parola</span></div>
      <div><b>${sec(ultimo?.tempi.totale_ms)}</b><span>ultima risposta</span></div>
      <div><b>${tot.length ? sec(Math.min(...tot)) : '—'}</b><span>la più veloce</span></div>
    </div>
    ${righeModelli ? `<div class="sequenza"><h3>Confronto modelli</h3><table class="modelli">
      <tr><th>Modello</th><th class="num">N</th><th class="num">Medio</th><th class="num">1ª parola</th><th class="num">tok/s</th></tr>${righeModelli}</table></div>` : ''}
    <div class="legenda"><span><i style="background:var(--tenue)"></i>ricerca</span><span><i style="background:var(--bordeaux-chiaro)"></i>attesa prima parola</span><span><i style="background:var(--bordeaux)"></i>generazione</span></div>
    ${voci.map((v) => {
      const t = v.tempi, r = t.ricerca_ms || 0, p = Math.max(0, (t.prima_parola_ms ?? t.totale_ms) - r), g = Math.max(0, t.totale_ms - r - p);
      const w = (x) => (x / max * 100).toFixed(1) + '%';
      return `<div class="riga-tempo" title="${esc(v.domanda)}">
        <span class="q">${esc(v.domanda)}</span><span class="t">${sec(t.totale_ms)}</span>
        <div class="barra"><i class="ricerca" style="width:${w(r)}"></i><i class="attesa" style="width:${w(p)}"></i><i class="gen" style="width:${w(g)}"></i></div>
        <span class="dett">${esc(v.modello)} · ricerca ${t.ricerca_ms} ms · 1ª parola ${sec(t.prima_parola_ms)}${t.tok_s ? ` · ${t.tok_s} tok/s` : ''} · ${esc(v.ora.slice(11))}</span>
      </div>`;
    }).join('') || '<div class="vuoto">Nessuna risposta da misurare.</div>'}`;
}

function disegnaDocumenti(lista, filtro) {
  // senza modalità debug l'elenco non dice quali file sono «sospetti» o «non pertinenti»: il detective lo scopre dalle risposte
  const pertinenti = stato.documenti.filter((d) => !d.consultazione).length;
  $('#conta-sinistra').textContent = DEBUG ? `${stato.documenti.length} file · ${pertinenti} pertinenti` : `${stato.documenti.length} file`;
  $('#conta-sinistra').title = DEBUG ? stato.server.pertinenza?.dettaglio || '' : '';
  // il filtro trova anche "sospetto", "non pertinente" e "pertinente" (solo in debug)
  const testoFiltro = (d) => `${d.nome} ${d.titolo} ${DEBUG ? `${d.sospetto ? 'sospetto' : ''} ${d.consultazione ? 'non pertinente' : 'pertinente'}` : ''} ${d.giorno ? `giorno ${d.giorno}` : ''}`.toLowerCase();
  const voci = stato.documenti.filter((d) => !filtro || testoFiltro(d).includes(filtro));
  lista.innerHTML = voci.map((d) => `
    <article class="voce" data-id="${esc(d.id)}">
      <div class="testa"><span>${esc(d.nome)}</span><span>
        ${DEBUG && d.sospetto ? `<span class="etichetta sospetto" title="${esc(d.sospetto)}${d.sospetto_manuale === true ? ' (segnato a mano)' : ''}">⚠ sospetto</span> ` : ''}
        ${DEBUG && d.consultazione ? `<span class="etichetta" title="Escluso dal clustering di pertinenza${d.pertinenza != null ? ` (somiglianza al caso ${d.pertinenza})` : ''}: il modello ne parla solo se glielo chiedi, e non diventa mai un indizio">non pertinente</span> ` : ''}
        ${d.giorno ? `<span class="etichetta" title="Giorno dell'indagine in cui è arrivato il documento">giorno ${d.giorno}</span> ` : ''}
        <span class="etichetta ${d.origine === 'caricato' ? 'bordeaux' : ''}">${d.origine}</span></span></div>
      <div class="domanda" data-documento>${esc(d.titolo)}</div>
      <div class="piede"><span class="etichetta">${d.caratteri} caratteri</span>
        <span>${DEBUG ? `<button class="btn-piccolo grigio" data-sospetto="${d.sospetto ? 'false' : 'true'}"
          title="${d.sospetto ? 'Non è un documento manipolatorio: togli il segnale' : 'Segna come documento manipolatorio'}">${d.sospetto ? 'Non sospetto' : 'Segna sospetto'}</button>
        ${d.sospetto_manuale != null ? '<button class="btn-piccolo grigio" data-sospetto="auto" title="Torna al riconoscimento automatico">Automatico</button> ' : ''}` : ''}
        ${d.origine === 'caricato' ? `<button class="btn-piccolo grigio" data-rimuovi>${ICONE.x}Rimuovi</button> ` : ''}<button class="btn-piccolo" data-documento>${ICONE.apri}Apri</button></span></div>
    </article>`).join('') || '<div class="vuoto">Nessun documento.</div>';
}

async function segnaSospetto(id, valore) {
  try {
    const r = await postJson('/api/documento/sospetto', { id, sospetto: valore });
    sistema(r.sospetto ? `${id} segnato come sospetto: ${r.sospetto}.` : `${id} non è più segnato come sospetto.`);
    await aggiornaTutto();
  } catch (err) { sistema('Errore: ' + err.message); }
}

async function rimuoviDocumento(id) {
  // la voce mostra subito che si sta lavorando: l'indice si ricostruisce (qualche secondo con un archivio grande)
  const voce = document.querySelector(`#lista-sinistra .voce[data-id="${CSS.escape(id)}"]`);
  const bottone = voce?.querySelector('[data-rimuovi]');
  if (bottone) { bottone.disabled = true; bottone.textContent = 'rimuovo…'; }
  voce?.classList.add('in-corso');
  try {
    const r = await postJson('/api/rimuovi', { id });
    document.querySelectorAll(`.file-chip[data-doc="${CSS.escape(id)}"]`).forEach((c) => c.remove());
    sistema(`Rimosso ${id}: indice aggiornato, ${r.documenti} documenti.`);
    await aggiornaTutto();
  } catch (err) {
    sistema('Errore nella rimozione: ' + err.message);
    if (bottone) { bottone.disabled = false; bottone.innerHTML = `${ICONE.x}Rimuovi`; }
    voce?.classList.remove('in-corso');
  }
}

// ------------------------------------------------------- pannello destro
const COLORI = ['#7b1e34', '#5c5c66', '#97304a', '#45454d', '#6b2537', '#73737d'];
const colore = (n) => COLORI[[...n].reduce((a, c) => a + c.charCodeAt(0), 0) % COLORI.length];
const iniziali = (n) => n.split(/\s+/).map((p) => p[0]).slice(0, 2).join('').toUpperCase();
// mentre il modello scrive si apre una nuova scheda: lasciare la pagina interromperebbe la risposta
const apri = (url) => (stato.occupato ? window.open(url, '_blank') : vaiA(url));
const apriPersona = (nome) => apri(`persona.html?nome=${encodeURIComponent(nome)}`);
const apriElemento = (tipo, chiave) => apri(`persona.html?tipo=${tipo}&chiave=${encodeURIComponent(chiave)}`);
const docs = (n) => `${n} document${n === 1 ? 'o' : 'i'}`;

function vocePersona(p) {
  return `<button class="persona-voce" data-tipo="persone" data-chiave="${esc(p.nome)}" title="Apri i passaggi in cui compare ${esc(p.nome)}">
      ${p.volto ? voltoHtml(p.volto, 42) : `<span class="iniziali" style="background:${colore(p.nome)}">${esc(iniziali(p.nome))}</span>`}
      <span class="info"><b>${esc(p.nome)}</b>
        <span>${p.nei_documenti ? docs(p.documenti) : '<span class="etichetta attenzione">non nei documenti</span>'}</span></span>
      <span class="numero" title="risposte che la citano">${p.citazioni}</span>
    </button>`;
}

function voceElemento(tipo, e) {
  const sotto = !e.verificato ? '<span class="etichetta attenzione">da verificare</span>'
    : tipo === 'luoghi' ? docs(e.documenti)
    : `<span class="estratto"><span class="etichetta bordeaux">${esc(e.doc)}</span> ${esc(e.estratto)}</span>`;
  return `<button class="persona-voce" data-tipo="${tipo}" data-chiave="${esc(e.chiave)}" title="Apri i passaggi dei documenti">
      <span class="icona-tipo">${ICONE[tipo]}</span>
      <span class="info"><b>${esc(e.nome)}</b><span>${sotto}</span></span>
      <span class="numero" title="risposte che lo citano">${e.citazioni}</span>
    </button>`;
}

function disegnaElementi() {
  const tipo = stato.tipoDestra;
  const filtro = $('#filtro-persone').value.trim().toLowerCase();
  document.querySelectorAll('#schede-elementi button').forEach((b) => {
    b.classList.toggle('attiva', b.dataset.tipo === tipo);
    b.querySelector('span').textContent = stato.elementi[b.dataset.tipo]?.length || '';
  });
  const tutte = stato.elementi[tipo] || [];
  const voci = tutte.filter((e) => !filtro || (e.nome + ' ' + (e.estratto || '')).toLowerCase().includes(filtro));
  $('#conta-persone').textContent = Object.values(stato.elementi).reduce((a, l) => a + l.length, 0) + ' totali';
  $('#lista-persone').innerHTML = voci.map((e) => (tipo === 'persone' ? vocePersona(e) : voceElemento(tipo, e))).join('')
    || `<div class="vuoto">${filtro ? 'Nessun risultato.' : `${NOMI_TIPO[tipo]} citati nelle risposte compariranno qui.`}</div>`;
}
document.querySelectorAll('#schede-elementi button').forEach((b) => b.addEventListener('click', () => {
  stato.tipoDestra = b.dataset.tipo;
  $('#filtro-persone').value = '';
  disegnaElementi();
}));
$('#filtro-persone').addEventListener('input', disegnaElementi);
$('#lista-persone').addEventListener('click', (e) => {
  const b = e.target.closest('.persona-voce');
  if (!b) return;
  b.dataset.tipo === 'persone' ? apriPersona(b.dataset.chiave) : apriElemento(b.dataset.tipo, b.dataset.chiave);
});

// ------------------------------------------------------------------ chat
const messaggi = $('#messaggi');
const PROVE = ['Cosa è successo?', 'Quale persona ha lasciato un messaggio?', 'Quali informazioni mancano?'];

function benvenuto() {
  messaggi.innerHTML = `<div class="benvenuto">
    <h2>Pronto a indagare</h2>
    <p>Ragiono sui documenti e cito le fonti. Con <b>+</b> aggiungi file da esaminare.</p>
    <div>${PROVE.map((p) => `<button class="chip" data-prova>${esc(p)}</button>`).join('')}</div></div>`;
}
messaggi.addEventListener('click', (e) => {
  const prova = e.target.closest('[data-prova]');
  if (prova) return chiedi(prova.textContent);
  const cit = e.target.closest('[data-doc]');
  if (cit) return apri(`persona.html?doc=${encodeURIComponent(cit.dataset.doc)}`);
  const p = e.target.closest('[data-persona]');
  if (p) return apriPersona(p.dataset.persona);
  const el = e.target.closest('[data-elemento]');
  if (el) apriElemento(el.dataset.elemento, el.dataset.chiave);
});

function scorri() { messaggi.scrollTop = messaggi.scrollHeight; }
function ora() { return new Date().toLocaleTimeString('it-IT', { hour: '2-digit', minute: '2-digit' }); }

function sistema(testo) {
  $('.benvenuto', messaggi)?.remove();
  messaggi.insertAdjacentHTML('beforeend', `<div class="msg sistema">${esc(testo)}</div>`);
  scorri();
}

// testo del modello -> HTML: Markdown (comune.js), citazioni [G1-02] cliccabili e persone evidenziate
// Il modello cita i documenti col nome del file ([G1-02]) o con l'ID scritto dentro ([ARCH-6408] = documento_0421,
// [LX4-7783] = LX4_7783): si confrontano senza trattini, spazi e maiuscole.
const chiaveId = (t) => t.toLowerCase().replace(/[\W_]+/g, '');
function documentoCitato(testo) {
  const k = chiaveId(testo.trim().replace(/\.(txt|md|docx|pdf|csv|jsonl?|xml|html?|xlsx)$/i, ''));
  if (!k) return null;
  const doc = stato.documenti.find((x) => chiaveId(x.id) === k) || stato.documenti.find((x) => x.id_interno && chiaveId(x.id_interno) === k);
  return doc ? doc.id : null;
}

function formatta(testo, persone = []) {
  let h = markdown(testo);
  h = h.replace(/\[([^\[\]\n]{1,60})\]/g, (m, interno) => interno.split(/[,;]\s*/).map((d) => {
    const id = documentoCitato(d);
    return id ? `<span class="cit" data-doc="${esc(id)}" title="${esc(id)}">${esc(d.trim())}</span>` : `[${esc(d)}]`;
  }).join(' '));
  for (const p of persone) {
    const re = new RegExp(`(^|[^\\wÀ-ÿ>])(${p.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})(?![\\wÀ-ÿ])`, 'g');
    h = h.replace(re, `$1<span class="persona" data-persona="${esc(p)}">$2</span>`);
  }
  return h;
}

function aggiungiUtente(testo, rieseguita, orario = ora()) {
  $('.benvenuto', messaggi)?.remove();
  messaggi.insertAdjacentHTML('beforeend', `<div class="msg utente"><span class="ora">${orario}${rieseguita ? ' · rieseguita' : ''}</span>${esc(testo)}</div>`);
}

function nuovaRisposta(orario = ora()) {
  const el = document.createElement('div');
  el.className = 'msg modello';
  el.innerHTML = `<span class="ora">${orario}</span><div class="chi"><span class="avatar" style="width:30px;height:30px">
    <svg viewBox="0 0 24 24" style="width:17px;height:17px"><path d="M4 10h16M6 10l1.5-5h9L18 10"/><circle cx="8" cy="15" r="3"/><circle cx="16" cy="15" r="3"/></svg></span>Investigatore</div>
    <div class="corpo cursore"></div><div class="extra"><div class="fila stato-ricerca">cerco nei documenti…</div></div>`;
  messaggi.appendChild(el);
  scorri();
  return el;
}

function completaRisposta(el, voce) {
  const corpo = $('.corpo', el);
  corpo.classList.remove('cursore');
  corpo.innerHTML = formatta(voce.risposta, voce.persone) || '<span style="color:var(--debole)">(nessun testo)</span>';
  const t = voce.tempi;
  el.querySelector('.extra').innerHTML = `
    ${voce.errore ? `<div class="errore">${esc(voce.errore)}</div>` : ''}
    ${voce.fonti.length ? `<div class="fila">Fonti: ${voce.fonti.map((f) => {
      const sospetto = DEBUG && stato.documenti.find((d) => d.id === f.doc)?.sospetto;   // documenti che danno ordini all'assistente (solo in debug)
      return `<span class="chip${sospetto ? ' sospetto' : ''}" data-doc="${esc(f.doc)}" title="${esc(sospetto ? `SOSPETTO: ${sospetto}\n\n${f.estratto}` : f.estratto)}">${sospetto ? '⚠ ' : ICONE.file}${esc(f.doc)}</span>`;
    }).join('')}</div>` : ''}
    ${voce.persone.length ? `<div class="fila">Persone: ${voce.persone.map((p) => `<span class="chip persona" data-persona="${esc(p)}">${esc(p)}</span>`).join('')}</div>` : ''}
    <div class="tempi-msg">prima parola <b>${sec(t.prima_parola_ms)}</b> · totale <b>${sec(t.totale_ms)}</b> · ricerca ${t.ricerca_ms} ms${t.tok_s ? ` · ${t.tok_s} tok/s` : ''} · ${esc(voce.modello)}</div>
    <details class="dett-strumenti"><summary>${voce.strumenti.length} strumenti usati</summary>
      ${voce.strumenti.map((s, i) => `<div class="passo"><span class="n">${i + 1}</span><span><code>${esc(s.nome)}</code> — ${esc(s.dettaglio)}</span><span class="etichetta">${s.ms} ms</span></div>`).join('')}
    </details>`;
  scorri();
}

async function chiedi(domanda, rieseguitaDa = null) {
  domanda = domanda.trim();
  if (!domanda || stato.occupato) return;
  stato.occupato = true;
  $('#invia').disabled = true;
  aggiungiUtente(domanda, rieseguitaDa);
  const el = nuovaRisposta();
  const corpo = $('.corpo', el);
  let testo = '';
  try {
    const r = await fetch('/api/chiedi', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      // vista = scambi visibili in chat: sono la memoria per "cosa ti ho appena chiesto?" (Pulisci la vista la azzera)
      body: JSON.stringify({ domanda, modello: $('#modello').value, rieseguita_da: rieseguitaDa, vista: leggiVista() }),
    });
    if (!r.ok) throw new Error((await r.json()).errore);
    const lettore = r.body.getReader();
    const dec = new TextDecoder();
    let buffer = '';
    for (;;) {
      const { value, done } = await lettore.read();
      if (done) break;
      buffer += dec.decode(value, { stream: true });
      const righe = buffer.split('\n');
      buffer = righe.pop();
      for (const riga of righe) {
        if (!riga.trim()) continue;
        const ev = JSON.parse(riga);
        if (ev.tipo === 'fonti') $('.stato-ricerca', el).textContent = ev.fonti.length ? `${ev.fonti.length} brani trovati · scrivo la risposta…` : 'scrivo la risposta…';
        else if (ev.tipo === 'token') { testo += ev.testo; corpo.innerHTML = formatta(testo); scorri(); }
        else if (ev.tipo === 'fine') {
          completaRisposta(el, ev.voce);
          ricordaInVista(ev.voce.id);
          libera();      // la risposta è completa: si può già fare un'altra domanda
          if (!ev.voce.errore) el.querySelector('.extra').insertAdjacentHTML('beforeend', '<div class="fila riga-elementi">cerco luoghi, indizi ed eventi…</div>');
        } else if (ev.tipo === 'elementi') {
          mostraElementi(el, ev.elementi);
          aggiornaTutto().catch(() => {});
        }
      }
    }
    $('.riga-elementi', el)?.remove();   // estrazione non arrivata (interrotta)
  } catch (err) {
    corpo.classList.remove('cursore');
    el.querySelector('.extra').innerHTML = `<div class="errore">${esc(err.message)}</div>`;
  } finally {
    libera();
  }
}

function libera() {
  if (!stato.occupato) return;
  stato.occupato = false;
  $('#invia').disabled = false;
  aggiornaTutto().catch(() => {});
}

// stessa normalizzazione del server: minuscole, senza accenti, spazi singoli
const chiave = (t) => t.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/\s+/g, ' ').trim().replace(/^[ .,;:!?«»"]+|[ .,;:!?«»"]+$/g, '');

function mostraElementi(el, elementi) {
  const righe = ['luoghi', 'indizi', 'eventi'].filter((t) => elementi[t]?.length).map((t) => `<div class="fila">${NOMI_TIPO[t]}: ${
    elementi[t].map((e) => {
      const nome = e.nome || e.titolo;
      return `<span class="chip ${t}" data-elemento="${t}" data-chiave="${esc(chiave(nome))}" title="${esc(e.citazione || '')}">${ICONE[t]}${esc(nome)}${e.verificato === false ? ' ⚠' : ''}</span>`;
    }).join('')}</div>`).join('');
  const riga = $('.riga-elementi', el);
  if (riga) riga.outerHTML = righe || '';
  else el.querySelector('.extra').insertAdjacentHTML('beforeend', righe);
  scorri();
}

const area = $('#domanda');
function invia() { const t = area.value; area.value = ''; adatta(); chiedi(t); }
function adatta() { area.style.height = 'auto'; area.style.height = Math.min(area.scrollHeight, 140) + 'px'; }
area.addEventListener('input', adatta);
area.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); invia(); } });
$('#invia').addEventListener('click', invia);
$('#pulisci').addEventListener('click', benvenuto);

// ------------------------------------------------------------- file (+)
$('#piu').addEventListener('click', () => $('#file').click());
$('#file').addEventListener('change', (e) => { carica([...e.target.files]); e.target.value = ''; });
$('#piu-cartella').addEventListener('click', () => $('#cartella').click());
$('#cartella').addEventListener('change', (e) => {
  // webkitRelativePath = "Cartella/sotto/file.txt"
  caricaCartelle([...e.target.files].map((f) => ({ file: f, percorso: f.webkitRelativePath || f.name })));
  e.target.value = '';
});

const ESTENSIONI_OK = ['.txt', '.md', '.csv', '.json', '.jsonl', '.log', '.xml', '.html', '.htm', '.docx', '.pdf', '.xlsx', '.zip'];
const estensioneOk = (nome) => ESTENSIONI_OK.some((e) => nome.toLowerCase().endsWith(e));

// Cartelle: un chip per cartella, i file vanno su uno alla volta e l'indice si ricostruisce una volta sola alla fine.
async function caricaCartelle(voci) {
  const gruppi = new Map();
  for (const v of voci) {
    const radice = v.percorso.split('/')[0];
    if (!gruppi.has(radice)) gruppi.set(radice, []);
    gruppi.get(radice).push(v);
  }
  for (const [nome, tutti] of gruppi) {
    const buoni = tutti.filter((v) => estensioneOk(v.file.name));
    const saltati = tutti.length - buoni.length;
    if (!buoni.length) { sistema(`${nome}: nessun file leggibile (txt, md, csv, json, xml, html, docx, pdf, xlsx, zip).`); continue; }
    const chip = document.createElement('span');
    chip.className = 'file-chip carico';
    $('#file-in-esame').appendChild(chip);
    const salvati = [], errori = [];
    for (const [i, v] of buoni.entries()) {
      chip.innerHTML = `${ICONE.file}<span>${esc(nome)} · carico ${i + 1}/${buoni.length}…</span>`;
      try {
        const r = await api('/api/carica', { method: 'POST', body: v.file, headers: {
          'X-Nome-File': encodeURIComponent(v.file.name), 'X-Percorso': encodeURIComponent(v.percorso), 'X-Senza-Indice': '1' } });
        salvati.push(...r.salvati);
      } catch (err) { errori.push(`${v.percorso}: ${err.message}`); }
    }
    if (!salvati.length) { chip.remove(); sistema(`${nome}: nessun file caricato.\n${errori.join('\n')}`); continue; }
    chip.innerHTML = `${ICONE.file}<span>${esc(nome)} · aggiorno l'indice…</span>`;
    try {
      const r = await postJson('/api/reindicizza', { nome, file: salvati.length });
      chip.className = 'file-chip';
      chip.title = salvati.join('\n');
      chip.innerHTML = `${ICONE.file}<span>${esc(nome)} (${salvati.length} file)</span>`;
      sistema(`In esame: cartella ${nome} → ${salvati.length} file aggiunti`
        + (saltati ? `, ${saltati} saltati perché di formato non supportato` : '')
        + `, indice di ${r.documenti} documenti (${r.ms} ms).` + (errori.length ? `\nNon caricati:\n${errori.join('\n')}` : ''));
    } catch (err) { chip.remove(); sistema(`${nome}: ${err.message}`); }
  }
  aggiornaTutto().catch(() => {});
}

// Trascinando, le cartelle arrivano come voci da esplorare (sottocartelle comprese).
async function vociTrascinate(dt) {
  const entrate = [...dt.items].map((it) => it.webkitGetAsEntry && it.webkitGetAsEntry()).filter(Boolean);
  if (!entrate.some((e) => e.isDirectory)) return null;     // solo file: caricamento normale
  const voci = [], file = [];
  const leggiTutte = (lettore) => new Promise((ok, ko) => {
    const tutte = [];
    const giro = () => lettore.readEntries((parte) => { if (!parte.length) return ok(tutte); tutte.push(...parte); giro(); }, ko);
    giro();
  });
  async function visita(e, percorso) {
    if (e.isFile) {
      const f = await new Promise((ok, ko) => e.file(ok, ko));
      voci.push({ file: f, percorso: percorso + f.name });
    } else if (e.isDirectory) {
      for (const figlio of await leggiTutte(e.createReader())) await visita(figlio, `${percorso}${e.name}/`);
    }
  }
  for (const e of entrate) {
    if (e.isDirectory) await visita(e, '');
    else file.push(await new Promise((ok, ko) => e.file(ok, ko)));
  }
  return { voci, file };
}

async function carica(files) {
  for (const f of files) {
    const chip = document.createElement('span');
    chip.className = 'file-chip carico';
    chip.innerHTML = `${ICONE.file}<span>${esc(f.name)} · carico…</span>`;
    $('#file-in-esame').appendChild(chip);
    try {
      const r = await api('/api/carica', { method: 'POST', headers: { 'X-Nome-File': encodeURIComponent(f.name) }, body: f });
      const ids = r.salvati.map((s) => s.split(/[\\/]/).pop().replace(/\.[^.]+$/, ''));
      chip.className = 'file-chip';
      chip.dataset.doc = ids.length === 1 ? ids[0] : '';
      chip.title = r.salvati.join('\n');
      chip.innerHTML = `${ICONE.file}<span>${esc(f.name)}${ids.length > 1 ? ` (${ids.length} file)` : ''}</span>`
        + (ids.length === 1 ? `<button title="Togli dall'esame">${ICONE.x}</button>` : '');
      chip.querySelector('button')?.addEventListener('click', () => rimuoviDocumento(ids[0]));
      sistema(`In esame: ${f.name} → ${r.salvati.length} file aggiunti, indice di ${r.documenti} documenti (${r.ms} ms).`);
    } catch (err) {
      chip.remove();
      sistema(`${f.name}: ${err.message}`);
    }
  }
  aggiornaTutto().catch(() => {});
}

// trascina e rilascia sulla chat
const zona = $('#zona-chat');
let profondita = 0;
zona.addEventListener('dragenter', (e) => { if ([...e.dataTransfer.types].includes('Files')) { profondita++; $('#trascina').classList.remove('nascosto'); } });
zona.addEventListener('dragleave', () => { if (--profondita <= 0) { profondita = 0; $('#trascina').classList.add('nascosto'); } });
zona.addEventListener('dragover', (e) => e.preventDefault());
zona.addEventListener('drop', async (e) => {
  e.preventDefault(); profondita = 0; $('#trascina').classList.add('nascosto');
  const soloFile = [...e.dataTransfer.files];
  const trascinati = await vociTrascinate(e.dataTransfer).catch(() => null);
  if (!trascinati) return carica(soloFile);
  if (trascinati.file.length) await carica(trascinati.file);
  caricaCartelle(trascinati.voci);
});

// ------------------------------------------- chat ritrovata tornando indietro
// Le schede si aprono nella stessa finestra: le risposte visibili si ricordano (solo in questa scheda del browser)
// e, tornando con la freccia indietro, si ridisegnano dallo storico.
const CHIAVE_VISTA = 'lazzaro-vista';
const leggiVista = () => { try { return JSON.parse(sessionStorage.getItem(CHIAVE_VISTA)) || []; } catch { return []; } };
const scriviVista = (ids) => { try { sessionStorage.setItem(CHIAVE_VISTA, JSON.stringify(ids)); } catch { /* memoria non disponibile */ } };
const ricordaInVista = (id) => scriviVista([...leggiVista().filter((x) => x !== id), id]);

function ripristinaVista() {
  const voci = leggiVista().map((id) => stato.storico.find((v) => v.id === id)).filter(Boolean);
  for (const v of voci) {
    const orario = (v.ora || '').slice(11, 16);
    aggiungiUtente(v.domanda, v.rieseguita_da, orario);
    const el = nuovaRisposta(orario);
    completaRisposta(el, v);
    if (v.elementi) mostraElementi(el, v.elementi);
  }
}
$('#pulisci').addEventListener('click', () => scriviVista([]));
document.querySelectorAll('.accessi a').forEach((a) => a.addEventListener('click', (e) => {   // mentre il modello scrive: nuova scheda
  if (stato.occupato) { e.preventDefault(); window.open(a.getAttribute('href'), '_blank'); }
}));

// ---------------------------------------------------------------- avvio
benvenuto();
aggiornaTutto().then(ripristinaVista).catch((err) => sistema('Server non raggiungibile: ' + err.message));
setInterval(() => { if (!stato.occupato) api('/api/stato').then((s) => { stato.server = s; disegnaStato(); }).catch(() => {}); }, 10000);
