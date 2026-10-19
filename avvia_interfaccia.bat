@echo off
rem Avvia l'investigatore LAZZARO in locale e apre il browser quando il server e' pronto.

rem ---- Modelli Ollama da usare: cambiali in base al PC ----
set "MODELLO=gemma4:12b"
set "EMBEDDING=bge-m3"
rem Prima di avviare: ollama pull %MODELLO%   e   ollama pull %EMBEDDING%
rem Per provare senza modello:  avvia_interfaccia.bat --demo

cd /d "%~dp0"

set "PY="
rem Con uv: stesso Python e `requests` di app_lazarus (che fornisce modello e istruzioni)
where uv >nul 2>nul && set "PY=uv run --python 3.12 --with requests"
if not defined PY where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY (
  echo Python non trovato. Installalo da python.org ^(spunta "Add python.exe to PATH"^) e riprova.
  pause
  exit /b 1
)

%PY% interfaccia\server.py --apri --modello %MODELLO% --embedding %EMBEDDING% %*
if errorlevel 1 (
  echo.
  echo Il server si e' fermato con un errore: leggi il messaggio qui sopra.
  pause
)
