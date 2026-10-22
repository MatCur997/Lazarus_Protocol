"""Protocollo di pertinenza: quali documenti caricati riguardano l'indagine, deciso dal contenuto (clustering),
non dall'origine dei file. Nessun filtro su "wikipedia" o sul campo source: funziona con qualunque archivio.

1. Un vettore per documento: media degli embedding (bge-m3) dei suoi brani.
2. Centratura: si toglie il vettore medio dell'archivio. In un archivio fatto quasi tutto di voci estranee
   (1000 su 1024) la media è "rumore di fondo"; tolta quella restano le differenze di contenuto.
3. k-means (k-means++ con più ripartenze, si tiene la migliore) con K ≈ √(N/2) cluster.
4. Profilo del caso: il vettore (centrato) della descrizione dello scenario nelle istruzioni del modello.
   Livello 1: i cluster il cui centroide è vicino al profilo (soglia di Otsu) formano un primo dominio dell'indagine.
5. Livello 2: si tolgono i documenti più vicini al centroide degli estranei (solo togliere: aggiungere fa scivolare
   il confronto verso l'archivio estraneo); poi rientra chi ha la maggioranza dei 5 vicini nel dominio (vicinato).
6. Livello 3: coerenza di cartella. In una cartella con almeno il 60% di documenti dell'indagine lo sono tutti
   (testi generici come G2-07 "Regola dell'indagine" stanno sul confine, ma arrivano insieme agli altri G2).
7. Archivi piccoli (meno di MINIMO documenti): tutti pertinenti, non c'è niente da separare.

Misure. Giorno 3 (1024 documenti: 24 del caso + 1000 voci enciclopediche): 24/24, 0 intrusi enciclopedici.
Con i 500 registri di routine del giorno 3 (stesso stile del caso): tutti i documenti G/ARCH presi, 0 voci
enciclopediche; i registri di routine restano nel dominio dell'indagine (sono materiale del caso: contro di loro
lavorano l'accorpamento delle copie e la ricerca a salti, non questo filtro). Selezionare un solo cluster non bastava:
con quei registri sceglieva un cluster di sola routine e scartava tutti i documenti del caso.
Con numpy (se c'è) servono decimi di secondo; senza, Python puro in background."""
import math
import random

MINIMO = 40          # sotto questo numero di documenti non si separa nulla
RIPARTENZE = 4       # k-means da più punti di partenza: si tiene la soluzione più compatta
GIRI_MAX = 50

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


def pertinenti(vettori_doc, profilo, cartelle=None):
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
    # livello 1: i cluster vicini al caso (soglia di Otsu sulla vicinanza) sono il primo "dominio dell'indagine"
    scelti = {c for c in range(K) if vicinanza[c] > otsu(vicinanza)}
    dentro = [etichette[j] in scelti for j in range(n)]
    # livello 2: si TOLGONO dal dominio i documenti più vicini al centroide degli estranei che a quello dell'indagine,
    # finché nessuno cambia. Solo togliere: aggiungendo, partendo da un gruppo piccolo, il confronto "scivola" verso
    # metà dell'archivio estraneo (misurato: 479 voci enciclopediche dentro e metà del caso fuori).
    giri = 0
    A = np.asarray(Xc) if np is not None else None
    for giri in range(1, 11):
        if all(dentro) or not any(dentro):
            break
        if A is not None:
            m = np.asarray(dentro)
            vicino_al_caso = ((A @ A[m].mean(axis=0)) > (A @ A[~m].mean(axis=0))).tolist()   # stessi confronti, con numpy
        else:
            c_in = _norma([sum(col) / sum(dentro) for col in zip(*[x for x, d in zip(Xc, dentro) if d])])
            c_out = _norma([sum(col) / (n - sum(dentro)) for col in zip(*[x for x, d in zip(Xc, dentro) if not d])])
            vicino_al_caso = [_dot(x, c_in) > _dot(x, c_out) for x in Xc]
        nuovo = [d and v for d, v in zip(dentro, vicino_al_caso)]
        if nuovo == dentro:
            break
        dentro = nuovo
    # livello 2b: vicinato. Entra un documento se la maggioranza dei suoi 5 documenti più simili è nel dominio:
    # i documenti del caso finiti fuori hanno per vicini altri documenti del caso, le voci estranee altre voci estranee.
    # Solo con numpy (servono tutte le somiglianze tra documenti).
    aggiunti = 0
    if A is not None and any(dentro):
        S = A @ A.T
        np.fill_diagonal(S, -1)
        vicini = np.argsort(-S, axis=1)[:, :5]
        for _ in range(3):
            m = np.asarray(dentro)
            nuovo = (m | (m[vicini].mean(axis=1) >= 0.6)).tolist()
            aggiunti += sum(nuovo) - sum(dentro)
            if nuovo == dentro:
                break
            dentro = nuovo
    # livello 3: coerenza di cartella. I documenti consegnati insieme di solito stanno insieme: se in una cartella
    # almeno il 60% dei file è dell'indagine, lo sono anche gli altri (G2-07 "Regola dell'indagine" è troppo generico
    # per il contenuto, ma sta con G2-01...G2-06). Le voci estranee restano fuori: nella loro cartella sono la maggioranza.
    recuperati = 0
    if cartelle:
        per_cartella = {}
        for j, d in enumerate(ids):
            per_cartella.setdefault(cartelle.get(d), []).append(j)
        for cartella, membri in per_cartella.items():
            if cartella is not None and len(membri) >= 2 and sum(dentro[j] for j in membri) / len(membri) >= 0.6:
                for j in membri:
                    recuperati += not dentro[j]
                    dentro[j] = True
    presi = {ids[j] for j in range(n) if dentro[j]}
    somiglianza = {ids[j]: _dot(Xc[j], pc) for j in range(n)}
    dettaglio = (f'k-means K={K} su {n} documenti, {len(scelti)} cluster vicini al caso, pulizia in {giri} giri'
                 + (f', {aggiunti} recuperati per vicinato' if aggiunti else '')
                 + (f', {recuperati} per cartella' if recuperati else '')
                 + f': {len(presi)} pertinenti, {n - len(presi)} esclusi')
    coordinate = _mappa_3d(Xc, pc)
    quota = {c: sum(dentro[j] for j in range(n) if etichette[j] == c) / max(1, etichette.count(c)) for c in range(K)}
    mappa = {'K': K, 'etichette': {ids[j]: etichette[j] for j in range(n)},
             'cluster': [{'id': c, 'n': etichette.count(c), 'vicinanza': round(vicinanza[c], 3), 'pertinente': quota[c] >= 0.5}
                         for c in range(K)],
             'coordinate': {ids[j]: [round(x, 4) for x in coordinate[j]] for j in range(n)} if coordinate else None}
    return presi, somiglianza, dettaglio, mappa
