// Tema chiaro (carta) / scuro (grigio scuro e bianco panna). Si carica in <head>, prima del CSS visibile:
// così la pagina compare subito con il tema scelto. La scelta resta salvata nel browser e vale anche per
// le schede che si aprono (persona.html), che stanno sullo stesso indirizzo.
(function () {
  const CHIAVE = 'lazzaro-tema';
  const radice = document.documentElement;
  const imposta = (tema) => { if (tema === 'scuro') radice.dataset.tema = 'scuro'; else delete radice.dataset.tema; };
  try {
    const salvato = localStorage.getItem(CHIAVE);
    if (salvato) imposta(salvato);
  } catch (e) { /* memoria del browser non disponibile: resta il tema chiaro */ }

  document.addEventListener('DOMContentLoaded', () => {
    const pulsante = document.getElementById('tema');
    if (!pulsante) return;
    pulsante.addEventListener('click', () => {
      const nuovo = radice.dataset.tema === 'scuro' ? 'chiaro' : 'scuro';
      imposta(nuovo);
      try { localStorage.setItem(CHIAVE, nuovo); } catch (e) { /* resta solo per questa pagina */ }
    });
  });
  // un'altra scheda ha cambiato tema: ci adeguiamo
  window.addEventListener('storage', (e) => { if (e.key === CHIAVE) imposta(e.newValue); });
})();

// Modalità debug, solo per il team. Di solito l'interfaccia NON dice al detective cosa è «sospetto» o «non pertinente»: lo scopre dalle
// risposte. Con la modalità debug (pulsante «Debug», oppure ?debug=1 nell'indirizzo; ?debug=0 la spegne) tornano etichette, colori e conteggi.
// `DEBUG` è disponibile a tutti gli script delle pagine; gli elementi con classe `solo-debug` si vedono solo con la modalità accesa.
var DEBUG = false;
(function () {
  const CHIAVE = 'lazzaro-debug';
  const radice = document.documentElement;
  try {
    const richiesta = new URLSearchParams(location.search).get('debug');
    if (richiesta === '1' || richiesta === '0') localStorage.setItem(CHIAVE, richiesta);
    DEBUG = localStorage.getItem(CHIAVE) === '1';
  } catch (e) { /* memoria del browser non disponibile: modalità debug spenta */ }
  if (DEBUG) radice.dataset.debug = '1';
  document.addEventListener('DOMContentLoaded', () => {
    const pulsante = document.getElementById('debug');
    if (!pulsante) return;
    pulsante.classList.toggle('acceso', DEBUG);
    pulsante.addEventListener('click', () => {
      try { localStorage.setItem(CHIAVE, DEBUG ? '0' : '1'); } catch (e) { return; }
      location.reload();         // le pagine disegnano le etichette all'avvio: si ricaricano
    });
  });
})();
