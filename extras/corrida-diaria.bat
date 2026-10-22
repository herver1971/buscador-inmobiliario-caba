@echo off
rem Corrida diaria para Windows: recolectar -> informe -> avisar.
rem La usa el Programador de tareas (ver README). Si un paso falla, los
rem siguientes no corren. Todo queda anotado en salida\corrida.log.
cd /d "%~dp0.."
set PYTHONIOENCODING=utf-8
if not exist salida mkdir salida
echo === corrida %date% %time% === >> salida\corrida.log
".venv\Scripts\python.exe" -m inmobiliario recolectar >> salida\corrida.log 2>&1
if errorlevel 1 exit /b 1
".venv\Scripts\python.exe" -m inmobiliario informe >> salida\corrida.log 2>&1
if errorlevel 1 exit /b 1
rem Si no configuraste Telegram, avisar solo lo dice y sigue.
".venv\Scripts\python.exe" -m inmobiliario avisar >> salida\corrida.log 2>&1
