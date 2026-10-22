"""Protocollo di pertinenza: quali documenti caricati riguardano l'indagine, deciso dal contenuto (clustering),
non dall'origine dei file. Nessun filtro su "wikipedia" o sul campo source: funziona con qualunque archivio.

1. Un vettore per documento: media degli embedding (bge-m3) dei suoi brani.
2. Centratura: si toglie il vettore medio dell'archivio. In un archivio fatto quasi tutto di voci estranee
   (1000 su 1024) la media è "rumore di fondo"; tolta quella restano le differenze di contenuto.
3. k-means (k-means++ con più ripartenze, si tiene la migliore) con K ≈ √(N/2) cluster.
4. Profilo del caso: il vettore (centrato) della descrizione dello scenario nelle istruzioni del modello.
   I cluster pertinenti sono quelli il cui centroide è più vicino al profilo (separati dagli altri con Otsu);
   se così si prenderebbe più di metà archivio, si tiene solo il cluster più vicino.
5. Rifinitura (RIFINITURA): tra i documenti presi si rifà un k-means a DUE gruppi e si tiene il gruppo i cui
   documenti somigliano di più al profilo. Serve quando il primo passaggio, largo, lascia dentro molti documenti estranei vicini per argomento al
   caso. Si applica solo se i due gruppi sono davvero distinti (coseno tra i centroidi sotto RIFINITURA_SEPARAZIONE_MAX); altrimenti non fa nulla.
6. Archivi piccoli (meno di MINIMO documenti): tutti pertinenti, non c'è niente da separare.

Misura sull'archivio della giornata 3 (1024 documenti, 24 del caso): pertinenti 24, del caso presi 24/24,
intrusi 0 (con K = 20 e 40). Con numpy (se c'è) servono decimi di secondo; senza, Python puro in background."""
import math
import random

MINIMO = 40          # sotto questo numero di documenti non si separa nulla (con K ≈ √(N/2) servono almeno una quarantina di punti perché i cluster dicano qualcosa)
RIPARTENZE = 4       # k-means da più punti di partenza: si tiene la soluzione più compatta
GIRI_MAX = 50
RIFINITURA = True                  # secondo passaggio a due gruppi dentro i pertinenti (vedi sopra); False = come prima
RIFINITURA_SEPARAZIONE_MAX = 0.5   # coseno massimo tra i centroidi dei due gruppi perché la divisione sia accettata

try:
    import numpy as np
except Exception:    # numpy assente (o bloccato): si usa Python puro
    np = None


def _norma(v):
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def otsu(valori):
    """Soglia che separa meglio due gruppi di valori (massima varianza tra i gruppi)."""
    s = sorted(valori)
    n, tot, cumulata = len(s), sum(s), 0.0
    migliore, soglia = -1.0, s[0]
    for k in range(1, n):
        cumulata += s[k - 1]
        m0, m1 = cumulata / k, (tot - cumulata) / (n - k)
        var = (k / n) * ((n - k) / n) * (m0 - m1) ** 2
        if var > migliore:
            migliore, soglia = var, (s[k - 1] + s[k]) / 2
    return soglia


def _scegli_centri(n, K, distanza_da, rnd):
    """k-means++: il primo centro a caso, gli altri preferendo i documenti lontani dai centri già scelti."""
    centri = [rnd.randrange(n)]
    distanze = distanza_da(centri[0])
    while len(centri) < K:
        pesi = [max(d, 0) ** 2 for d in distanze]
        r, acc, scelto = rnd.random() * (sum(pesi) or 1.0), 0.0, n - 1
        for j, p in enumerate(pesi):
            acc += p
            if acc >= r:
                scelto = j
                break
        centri.append(scelto)
        distanze = [min(a, b) for a, b in zip(distanze, distanza_da(scelto))]
    return centri


def _kmeans(X, K, seme):
    """k-means sferico (somiglianza coseno). Ritorna (etichette, centroidi, inerzia: somma di 1 - coseno)."""
    rnd = random.Random(seme)
    n = len(X)
    if np is not None:
        A = np.asarray(X)
        C = A[_scegli_centri(n, K, lambda j: (1 - A @ A[j]).tolist(), rnd)]
        etichette = None
        for _ in range(GIRI_MAX):
            nuove = np.argmax(A @ C.T, axis=1)
            if etichette is not None and np.array_equal(nuove, etichette):
                break
            etichette = nuove
            for c in range(K):
                membri = A[etichette == c]
                if len(membri):
                    m = membri.mean(axis=0)
                    C[c] = m / (np.linalg.norm(m) or 1.0)
        S = A @ C.T
        return etichette.tolist(), C.tolist(), float((1 - S[np.arange(n), etichette]).sum())
    centri = [X[j] for j in _scegli_centri(n, K, lambda j: [1 - _dot(x, X[j]) for x in X], rnd)]
    etichette = None
    for _ in range(GIRI_MAX):
        nuove = [max(range(K), key=lambda c: _dot(x, centri[c])) for x in X]
        if nuove == etichette:
            break
        etichette = nuove
        for c in range(K):
            membri = [X[j] for j in range(n) if etichette[j] == c]
            if membri:
                centri[c] = _norma([sum(col) / len(membri) for col in zip(*membri)])
    return etichette, centri, sum(1 - _dot(X[j], centri[etichette[j]]) for j in range(n))


def _mappa_3d(Xc, pc):
    """Coordinate 3D per la RAG view (solo con numpy): x = somiglianza al profilo del caso (più alto = più vicino),
    y e z = le due variazioni principali che restano togliendo la direzione del caso. Le prime componenti principali
    da sole mescolerebbero i documenti del caso con gli altri: descrivono soprattutto le differenze tra le voci estranee."""
    if np is None or len(Xc) < 4:
        return None
    A, p = np.asarray(Xc), np.asarray(pc)
    x = A @ p
    resto = A - np.outer(x, p)
    resto = resto - resto.mean(axis=0)
    _, _, vt = np.linalg.svd(resto, full_matrices=False)
    return np.column_stack([x, resto @ vt[0], resto @ vt[1]]).tolist()


def _rifinisci(Xc, pc, indici):
    """Dei documenti `indici` tiene il gruppo, tra due, più vicino al profilo. Il k-means a due gruppi lavora SENZA ricentrare sui presi (ricentrando,
    il gruppo del caso, molto compatto, e il resto si capovolgono). Se i due gruppi non sono distinti (centroidi troppo simili) tiene tutto.
    Rete di sicurezza: un documento scartato che somiglia al profilo PIÙ DI OGNI documento tenuto non può essere estraneo, e rientra (un documento
    del caso scritto diversamente dagli altri, per esempio il testo che dà l'incarico, finirebbe nel gruppo sbagliato)."""
    sotto = [Xc[j] for j in indici]
    etichette, centri, _ = min((_kmeans(sotto, 2, seme) for seme in range(RIPARTENZE)), key=lambda r: r[2])
    if _dot(centri[0], centri[1]) >= RIFINITURA_SEPARAZIONE_MAX:
        return indici
    giusto = max((0, 1), key=lambda c: _dot(centri[c], pc))
    tenuti = [j for j, e in zip(indici, etichette) if e == giusto]
    migliore = max(_dot(Xc[j], pc) for j in tenuti)
    ripresi = [j for j, e in zip(indici, etichette) if e != giusto and _dot(Xc[j], pc) > migliore]
    return sorted(tenuti + ripresi)


def pertinenti(vettori_doc, profilo):
    """vettori_doc: {doc_id: vettore}, profilo: vettore della descrizione del caso.
    Ritorna (insieme dei documenti pertinenti, {doc_id: somiglianza al profilo}, dettaglio per il pannello strumenti,
    mappa per la RAG view: {K, etichette {doc: cluster}, cluster [{id, n, vicinanza, pertinente}], coordinate {doc: [x, y, z]}})."""
    ids = list(vettori_doc)
    n = len(ids)
    if n < MINIMO:
        return set(ids), {d: 1.0 for d in ids}, f'{n} documenti: archivio piccolo, tutti pertinenti', None
    X = [_norma(vettori_doc[d]) for d in ids]
    media = [sum(col) / n for col in zip(*X)]
    Xc = [_norma([x - m for x, m in zip(v, media)]) for v in X]
    pc = _norma([x - m for x, m in zip(_norma(profilo), media)])
    K = max(2, round(math.sqrt(n / 2)))
    migliore = min((_kmeans(Xc, K, seme) for seme in range(RIPARTENZE)), key=lambda r: r[2])
    etichette, centri, _ = migliore
    vicinanza = [_dot(c, pc) for c in centri]
    scelti = {c for c in range(K) if vicinanza[c] > otsu(vicinanza)}
    if sum(etichette.count(c) for c in scelti) > n / 2:          # troppo largo: solo il cluster più vicino
        scelti = {max(range(K), key=vicinanza.__getitem__)}
    indici = [j for j in range(n) if etichette[j] in scelti]
    scartati_rifinitura = 0
    if RIFINITURA and len(indici) >= MINIMO:
        tenuti = _rifinisci(Xc, pc, indici)
        scartati_rifinitura = len(indici) - len(tenuti)
        indici = tenuti
    presi = {ids[j] for j in indici}
    somiglianza = {ids[j]: _dot(Xc[j], pc) for j in range(n)}
    dettaglio = (f'k-means K={K} su {n} documenti: {len(presi)} pertinenti in {len(scelti)} '
                 f'cluster vicini al caso, {n - len(presi)} esclusi'
                 + (f' (di cui {scartati_rifinitura} dalla rifinitura a due gruppi)' if scartati_rifinitura else ''))
    coordinate = _mappa_3d(Xc, pc)
    mappa = {'K': K, 'etichette': {ids[j]: etichette[j] for j in range(n)},
             'cluster': [{'id': c, 'n': etichette.count(c), 'vicinanza': round(vicinanza[c], 3), 'pertinente': c in scelti}
                         for c in range(K)],
             'coordinate': {ids[j]: [round(x, 4) for x in coordinate[j]] for j in range(n)} if coordinate else None}
    return presi, somiglianza, dettaglio, mappa
