#!/usr/bin/env bash
# Восстановление бота из копии scripts/backup.sh — на новом (или переустановленном) сервере.
#
#   cd ~/yamusicbot && bash scripts/restore.sh <file_id из Telegram>     — скачает архив из Telegram
#   cd ~/yamusicbot && bash scripts/restore.sh ~/yamusicbot-backup-….enc — архив уже на сервере
#
# Спросит токен бота (только для скачивания из Telegram) и пароль архива, разложит .env и data/ по местам
# и запустит бота. Без вопросов: BOT_TOKEN=… BACKUP_PASSWORD=… bash scripts/restore.sh …
set -euo pipefail
cd "$(dirname "$0")/.."

API="${TG_API:-https://api.telegram.org}"
CIPHER=(-aes-256-cbc -pbkdf2 -iter 200000 -md sha256)

for cmd in tar openssl curl; do
  command -v "$cmd" >/dev/null || { echo "Не найдена программа $cmd: apt install -y $cmd" >&2; exit 1; }
done

src="${1:-}"
if [ -z "$src" ]; then
  read -rp "Путь к архиву на сервере или file_id из Telegram: " src
fi

tmp=""
cleanup() { if [ -n "$tmp" ]; then rm -f "$tmp"; fi; }
trap cleanup EXIT

if [ -f "$src" ]; then
  archive="$src"
else
  # Не файл — значит, file_id документа, который бот прислал при копировании.
  if [ -z "${BOT_TOKEN:-}" ]; then
    read -rsp "Токен бота (из @BotFather; на экране не отображается): " BOT_TOKEN; echo
  fi
  echo "Скачиваю архив из Telegram…"
  info="$(curl -sS -m 60 --get --data-urlencode "file_id=$src" "$API/bot$BOT_TOKEN/getFile" || true)"
  path="$(printf '%s' "$info" | python3 -c 'import json,sys; print(json.load(sys.stdin)["result"]["file_path"])' 2>/dev/null || true)"
  if [ -z "$path" ]; then
    echo "Telegram не отдал файл: проверьте токен и file_id. Ответ: $(printf '%s' "$info" | head -c 200)" >&2
    exit 1
  fi
  tmp="$(mktemp)"
  curl -sS -m 600 -o "$tmp" "$API/file/bot$BOT_TOKEN/$path"
  archive="$tmp"
fi

existing=()
for item in .env data/bot.db; do
  if [ -e "$item" ]; then existing+=("$item"); fi
done
if [ "${#existing[@]}" -gt 0 ] && [ "${RESTORE_FORCE:-}" != 1 ]; then
  read -rp "Уже есть ${existing[*]} — перезаписать данными из копии? [y/N] " answer
  [[ "$answer" =~ ^[yYдД] ]] || { echo "Отменено, ничего не изменено."; exit 1; }
fi

if [ -z "${BACKUP_PASSWORD:-}" ]; then
  read -rsp "Пароль архива: " BACKUP_PASSWORD; echo
fi
export BACKUP_PASSWORD

# Сначала проверяем, что архив расшифровывается, и только потом что-то трогаем.
if ! listing="$(openssl enc -d "${CIPHER[@]}" -pass env:BACKUP_PASSWORD -in "$archive" 2>/dev/null | tar -tzf - 2>/dev/null)" \
    || [ -z "$listing" ]; then
  echo "Не получилось открыть архив: неверный пароль или файл повреждён. Ничего не изменено." >&2
  exit 1
fi
if command -v docker >/dev/null; then
  docker compose stop bot >/dev/null 2>&1 || true
fi
umask 077
openssl enc -d "${CIPHER[@]}" -pass env:BACKUP_PASSWORD -in "$archive" | tar -xzf -
echo "✅ Восстановлено: $(echo "$listing" | sed 's#^\./##' | cut -d/ -f1 | sort -u | tr '\n' ' ')"

if grep -qE '^DOMAIN=.+' .env; then
  echo "⚠️ В .env указан свой домен ($(grep -E '^DOMAIN=' .env | tail -n 1 | cut -d= -f2-)): направьте его A-запись на новый IP сервера."
fi

if [ "${RESTORE_NO_START:-}" = 1 ]; then
  exit 0
fi
if ! command -v docker >/dev/null; then
  echo "Docker ещё не установлен: curl -fsSL https://get.docker.com | sh — и затем: docker compose up -d --build"
  exit 0
fi
echo "Собираю и запускаю бота (первый раз — несколько минут)…"
docker compose up -d --build
echo "✅ Бот запущен. Проверить: docker compose logs --tail 30 bot"
