#!/bin/sh
# Corrida diaria para Linux/macOS: recolectar -> informe -> avisar.
# Sirve tanto para cron como para el timer de systemd (ver README).
# Si un paso falla, los siguientes no corren.
set -e
cd "$(dirname "$0")/.."
PY=".venv/bin/python"
mkdir -p salida
{
  echo "=== corrida $(date '+%Y-%m-%d %H:%M:%S') ==="
  "$PY" -m inmobiliario recolectar
  "$PY" -m inmobiliario informe
  # Si no configuraste Telegram, avisar solo lo dice y sigue.
  "$PY" -m inmobiliario avisar
} >> salida/corrida.log 2>&1
