// Investigatore LAZZARO - funzioni condivise da tutte le pagine (chat, schede, bacheca).
// Nessuna libreria esterna. Va caricato prima degli script delle pagine.
const $ = (s, r = document) => r.querySelector(s);
const esc = (t) => String(t ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const reEsc = (t) => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

// ------------------------------------------------------------- navigazione
// Le schede si aprono nella stessa finestra; le frecce in alto a sinistra tornano indietro o vanno avanti
// tra le pagine viste (come quelle del browser, utili quando l'app è aperta senza la barra del browser).
const vaiA = (url) => { location.href = url; };

function frecceNavigazione() {
  const riga = $('.riga-alta');
  if (!riga || $('.frecce', riga)) return;
  riga.insertAdjacentHTML('afterbegin', `<div class="frecce">
    <button class="tema-btn" data-verso="-1" title="Indietro (pagina precedente)" aria-label="Indietro"><svg viewBox="0 0 24 24"><path d="M15 18 9 12l6-6"/></svg></button>
    <button class="tema-btn" data-verso="1" title="Avanti (pagina successiva)" aria-label="Avanti"><svg viewBox="0 0 24 24"><path d="m9 18 6-6-6-6"/></svg></button></div>`);
  const [indietro, avanti] = riga.querySelectorAll('.frecce button');
  indietro.addEventListener('click', () => history.back());
  avanti.addEventListener('click', () => history.forward());
  const aggiorna = () => {   // la Navigation API dice se c'è una pagina prima/dopo; senza, si lasciano attive
    const nav = window.navigation;
    indietro.disabled = nav ? !nav.canGoBack : history.length < 2;
    avanti.disabled = nav ? !nav.canGoForward : false;
  };
  aggiorna();
  window.navigation?.addEventListener('currententrychange', aggiorna);
  addEventListener('pageshow', aggiorna);
}
frecceNavigazione();

// ------------------------------------------------------------------ volti
// Miniatura rotonda del volto di una persona: ritaglia il riquadro "viso" indicato in volti/volti.json.
// `volto` arriva dal server ({file, larghezza, altezza, viso: {x, y, lato}}); senza volto restano le iniziali.
function voltoHtml(volto, lato, classe = 'iniziali volto') {
  const k = lato / volto.viso.lato;
  const stile = [`width:${lato}px`, `height:${lato}px`, `background-image:url('${volto.file}')`,
    `background-size:${(volto.larghezza * k).toFixed(1)}px ${(volto.altezza * k).toFixed(1)}px`,
    `background-position:${(-(volto.viso.x - volto.viso.lato / 2) * k).toFixed(1)}px ${(-(volto.viso.y - volto.viso.lato / 2) * k).toFixed(1)}px`].join(';');
  return `<span class="${classe}" style="${stile}" role="img" aria-label="volto"></span>`;
}

// ------------------------------------------------------------------ Markdown
// Markdown (già con i caratteri speciali neutralizzati da esc): i simboli non restano visibili, diventano formattazione.
// Supporta titoli, elenchi, citazioni, tabelle, grassetto, corsivo, ==evidenziato==, barrato e `codice`.
const mdInline = (s) => s
  .replace(/`([^`]+)`/g, '<code>$1</code>')
  .replace(/\*\*(.+?)\*\*|__(.+?)__/g, (m, a, b) => `<b>${a ?? b}</b>`)
  .replace(/(^|[^*\w])\*(?![\s*])([^*]+?)\*(?![*\w])/g, '$1<i>$2</i>')
  .replace(/==(.+?)==/g, '<mark>$1</mark>')
  .replace(/~~(.+?)~~/g, '<s>$1</s>');

function markdown(testo) {
  const righe = esc(testo).replace(/\r/g, '').split('\n');
  const celle = (r) => r.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim());
  const sepTabella = (r) => r !== undefined && /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(r) && r.includes('-') && (r.includes('|') || /^\s*:?-{3,}/.test(r));
  let h = '', i = 0;
  while (i < righe.length) {
    const r = righe[i];
    let m;
    if (!r.trim()) { i++; continue; }
    if (r.includes('|') && sepTabella(righe[i + 1]) && righe[i + 1].includes('|')) {
      const testa = celle(r);
      i += 2;
      let corpo = '';
      while (i < righe.length && righe[i].trim() && righe[i].includes('|')) {
        corpo += `<tr>${celle(righe[i]).map((c) => `<td>${mdInline(c)}</td>`).join('')}</tr>`; i++;
      }
      h += `<table class="md-tab"><thead><tr>${testa.map((c) => `<th>${mdInline(c)}</th>`).join('')}</tr></thead><tbody>${corpo}</tbody></table>`;
    } else if ((m = r.match(/^\s*(#{1,6})\s+(.*)$/))) {
      h += `<div class="md-h md-h${Math.min(m[1].length, 3)}">${mdInline(m[2])}</div>`; i++;
    } else if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(r)) {
      h += '<hr>'; i++;
    } else if (/^\s*[-*•+]\s+/.test(r)) {
      let li = '';
      while (i < righe.length && /^\s*[-*•+]\s+/.test(righe[i])) { li += `<li>${mdInline(righe[i].replace(/^\s*[-*•+]\s+/, ''))}</li>`; i++; }
      h += `<ul>${li}</ul>`;
    } else if (/^\s*\d+[.)]\s+/.test(r)) {
      let li = '';
      while (i < righe.length && /^\s*\d+[.)]\s+/.test(righe[i])) { li += `<li>${mdInline(righe[i].replace(/^\s*\d+[.)]\s+/, ''))}</li>`; i++; }
      h += `<ol>${li}</ol>`;
    } else if (/^\s*&gt;\s?/.test(r)) {
      let q = [];
      while (i < righe.length && /^\s*&gt;\s?/.test(righe[i])) { q.push(mdInline(righe[i].replace(/^\s*&gt;\s?/, ''))); i++; }
      h += `<blockquote>${q.join('<br>')}</blockquote>`;
    } else {
      const p = [];
      while (i < righe.length && righe[i].trim() && !/^\s*(#{1,6}\s|[-*•+]\s|\d+[.)]\s|&gt;)/.test(righe[i])) { p.push(mdInline(righe[i])); i++; }
      h += `<p>${p.join('<br>')}</p>`;
    }
  }
  return h;
}

// testo senza simboli markdown, per le anteprime di una riga
const senzaMarkdown = (t) => String(t ?? '').replace(/^\s*#{1,6}\s+/gm, '').replace(/^\s*[-*•+]\s+/gm, '• ')
  .replace(/\*\*(.+?)\*\*|__(.+?)__/g, '$1$2').replace(/(^|[^*\w])\*(?![\s*])([^*]+?)\*(?![*\w])/g, '$1$2')
  .replace(/==(.+?)==|~~(.+?)~~|`([^`]+)`/g, '$1$2$3').replace(/^\s*\|?[\s:|-]{3,}\|?\s*$/gm, '').replace(/\|/g, ' ');

// Risposta del modello per le schede: Markdown formattato, [G1-02] come etichette, termini cercati evidenziati
// (solo nel testo, mai dentro i tag HTML).
function rispostaHtml(testo, termini = [], ignoraMaiuscole = false) {
  let h = markdown(testo).replace(/\[([^\[\]\n]{1,60})\]/g,
    (m, interno) => interno.split(/[,;]\s*/).map((d) => `<span class="etichetta bordeaux">${d.trim()}</span>`).join(' '));
  const validi = termini.filter(Boolean).sort((a, b) => b.length - a.length);
  if (!validi.length) return h;
  const re = new RegExp(`(${validi.map((t) => reEsc(esc(t))).join('|')})`, ignoraMaiuscole ? 'gi' : 'g');
  return h.split(/(<[^>]+>)/).map((pezzo) => (pezzo.startsWith('<') ? pezzo : pezzo.replace(re, '<mark>$1</mark>'))).join('');
}
