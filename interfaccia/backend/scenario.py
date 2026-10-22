"""Tutto ciò che dipende dallo scenario (formato dei documenti, codici, giorni, liste di parole italiane) in un solo posto.
Il resto del backend resta generico: per un'indagine diversa si cambia questo file, non la logica di ricerca.

Le costanti si spostano qui man mano, una fase alla volta (ogni modulo poi le importa da qui):
  - formato di ID e codici dei documenti      <- motore.py: RE_CODICE, RE_ID_DOCUMENTO
  - giorno dell'indagine                      <- indice.py: RE_GIORNO_CARTELLA, RE_GIORNO_TESTO, RE_GIORNO_ID
  - segnali di documenti sospetti             <- indice.py: SEGNALI_SOSPETTO
  - profilo del caso per la pertinenza        <- indice.py: profilo_del_caso (oggi: testo RUOLO delle istruzioni)
  - prefissi di luoghi, cose, santi e nomi    <- testo.py: PREFISSI_LUOGO, PREFISSI_COSA, PREFISSI_SANTI
  - nomi maschili in -a / femminili non in -a <- volti.py: MASCHILI_IN_A, FEMMINILI
"""
