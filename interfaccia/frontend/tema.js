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
