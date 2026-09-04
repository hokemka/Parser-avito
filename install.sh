#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/hokemka/Parser-avito.git}"
BRANCH="${BRANCH:-claude/avito-parser-bot-ai-ldzas0}"
INSTALL_DIR="${INSTALL_DIR:-$HOME/avito-bot}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

log() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
die() { printf '\033[1;31mОшибка: %s\033[0m\n' "$*" >&2; exit 1; }

ask() {
  local var="$1" prompt="$2" secret="${3:-0}" value
  if [ -n "${!var+x}" ]; then return; fi
  if [ ! -r /dev/tty ] || [ ! -w /dev/tty ]; then printf -v "$var" '%s' ""; return; fi
  if [ "$secret" = "1" ]; then
    read -r -s -p "$prompt: " value < /dev/tty; echo
  else
    read -r -p "$prompt: " value < /dev/tty
  fi
  printf -v "$var" '%s' "$value"
}

SUDO=""
if [ "$(id -u)" -ne 0 ]; then
  command -v sudo >/dev/null 2>&1 && SUDO="sudo"
fi

if [ "${SKIP_APT:-0}" != "1" ]; then
  log "Системные пакеты"
  $SUDO apt-get update -qq
  $SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git curl ca-certificates xvfb "$PYTHON_BIN" "$PYTHON_BIN-venv" python3-pip >/dev/null
fi

if [ -f "$(pwd)/main.py" ] && [ -f "$(pwd)/config.py" ]; then
  INSTALL_DIR="$(pwd)"
  log "Использую текущую папку: $INSTALL_DIR"
  git -C "$INSTALL_DIR" pull --ff-only origin "$BRANCH" 2>/dev/null || true
elif [ -d "$INSTALL_DIR/.git" ]; then
  log "Обновляю репозиторий в $INSTALL_DIR"
  git -C "$INSTALL_DIR" fetch origin "$BRANCH"
  git -C "$INSTALL_DIR" checkout -q "$BRANCH"
  git -C "$INSTALL_DIR" pull --ff-only origin "$BRANCH"
else
  log "Клонирую $REPO_URL ($BRANCH) в $INSTALL_DIR"
  git clone -b "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
fi
cd "$INSTALL_DIR"

log "Виртуальное окружение и зависимости"
[ -d .venv ] || "$PYTHON_BIN" -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

if [ "${SKIP_CAMOUFOX:-0}" != "1" ]; then
  log "Браузер Camoufox и его системные библиотеки"
  .venv/bin/python -m playwright install-deps firefox >/dev/null 2>&1 || $SUDO .venv/bin/python -m playwright install-deps firefox || true
  .venv/bin/python -m camoufox fetch
fi

if [ ! -f settings.ini ] || [ "${FORCE_SETTINGS:-0}" = "1" ]; then
  log "Настройки"
  ask BOT_TOKEN "Токен бота от @BotFather" 1
  ask ADMIN_IDS "ID администраторов через запятую"
  ask AI_API_KEY "Ключ API 1min.ai (Enter — пропустить)" 1
  ask CRYPTOBOT_TOKEN "Токен Crypto Pay (Enter — пропустить)" 1
  ask AVITO_PROXY "Прокси для Авито, например http://user:pass@host:port (Enter — без прокси)"
  .venv/bin/python - <<'PY'
import configparser, os
cfg = configparser.ConfigParser(interpolation=None)
cfg.read("settings.example.ini", encoding="utf-8")
cfg["bot"]["token"] = os.environ["BOT_TOKEN"].strip()
cfg["bot"]["admin_ids"] = os.environ["ADMIN_IDS"].strip()
cfg["bot"]["proxy"] = os.environ.get("TELEGRAM_PROXY", "").strip()
cfg["ai"]["api_key"] = os.environ.get("AI_API_KEY", "").strip()
cfg["ai"]["model"] = os.environ.get("AI_MODEL", "qwen3-8b").strip()
crypto = os.environ.get("CRYPTOBOT_TOKEN", "").strip()
cfg["payments"]["cryptobot_token"] = crypto
cfg["payments"]["cryptobot_enabled"] = "true" if crypto else "false"
cfg["avito"]["engine"] = os.environ.get("AVITO_ENGINE", "camoufox").strip()
cfg["avito"]["headless"] = os.environ.get("AVITO_HEADLESS", "true").strip()
cfg["avito"]["proxy"] = os.environ.get("AVITO_PROXY", "").strip()
with open("settings.ini", "w", encoding="utf-8") as f:
    cfg.write(f)
print("settings.ini записан")
PY
else
  log "settings.ini уже есть — оставляю как есть (FORCE_SETTINGS=1, чтобы перезаписать)"
fi

chmod +x bot.sh
mkdir -p data

if [ "${SKIP_START:-0}" != "1" ]; then
  log "Запуск"
  ./bot.sh restart
  sleep 6
  ./bot.sh status
  echo
  echo "Лог:        $INSTALL_DIR/bot.sh log"
  echo "Остановить: $INSTALL_DIR/bot.sh stop"
  echo "Запустить:  $INSTALL_DIR/bot.sh start"
fi
