#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
PID_FILE="data/bot.pid"
LOG_FILE="data/bot.log"
mkdir -p data

running() {
  [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null
}

case "${1:-status}" in
  start)
    if running; then echo "Бот уже запущен (PID $(cat "$PID_FILE"))"; exit 0; fi
    nohup .venv/bin/python main.py >> "$LOG_FILE" 2>&1 &
    echo $! > "$PID_FILE"
    echo "Бот запущен (PID $!), лог: $LOG_FILE"
    ;;
  stop)
    if running; then
      kill "$(cat "$PID_FILE")" && sleep 2
      running && kill -9 "$(cat "$PID_FILE")" || true
      echo "Бот остановлен"
    else
      echo "Бот не запущен"
    fi
    rm -f "$PID_FILE"
    ;;
  restart)
    "$0" stop
    "$0" start
    ;;
  status)
    if running; then
      echo "Бот работает (PID $(cat "$PID_FILE"))"
      tail -n 5 "$LOG_FILE" 2>/dev/null || true
    else
      echo "Бот не запущен"
      tail -n 15 "$LOG_FILE" 2>/dev/null || true
    fi
    ;;
  log)
    tail -n 100 -f "$LOG_FILE"
    ;;
  *)
    echo "Использование: $0 {start|stop|restart|status|log}"
    exit 1
    ;;
esac
