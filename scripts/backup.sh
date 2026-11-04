#!/usr/bin/env bash
# Резервная копия бота: .env (токены и ключ шифрования), база (data/) и ключ SSH-туннеля (ssh/, если есть)
# в один зашифрованный паролем архив. Если на сервере работает Claude Code, в архив попадают и его переписка
# с памятью (~/.claude без файла входа), а также crontab. Архив остаётся в домашней папке (последние
# BACKUP_KEEP копий, по умолчанию 7) и приходит вам в Telegram от бота.
#
#   cd ~/yamusicbot && bash scripts/backup.sh
#
# Восстановить на новом сервере — scripts/restore.sh (README, «Переезд и резервная копия»).
# Без вопросов (для cron): BACKUP_PASSWORD_FILE=~/.yamusicbot-backup-pass bash scripts/backup.sh
# Без Claude Code в архиве: BACKUP_CLAUDE=0.
set -euo pipefail
cd "$(dirname "$0")/.."

API="${TG_API:-https://api.telegram.org}"
CIPHER=(-aes-256-cbc -pbkdf2 -iter 200000 -md sha256)

for cmd in tar openssl curl; do
  command -v "$cmd" >/dev/null || { echo "Не найдена программа $cmd: apt install -y $cmd" >&2; exit 1; }
done
[ -f .env ] || { echo "Нет файла .env — запускайте из папки бота: cd ~/yamusicbot && bash scripts/backup.sh" >&2; exit 1; }
[ -d data ] || { echo "Нет папки data — похоже, бот ещё ни разу не запускался" >&2; exit 1; }

items=(.env data)
for extra in ssh docker-compose.override.yml; do
  if [ -e "$extra" ]; then items+=("$extra"); fi
done

env_value() {  # значение переменной из .env без кавычек (пусто, если её нет)
  { grep -E "^$1=" .env || true; } | tail -n 1 | cut -d= -f2- | tr -d "\"' \r"
}

notify() {  # короткое сообщение владельцу в Telegram — чтобы ночной сбой не прошёл незамеченным
  local token admin
  token="$(env_value BOT_TOKEN)"
  admin="$(env_value ADMIN_IDS | cut -d, -f1)"
  if [ -n "$token" ] && [ -n "$admin" ]; then
    curl -sS -m 30 -o /dev/null --data-urlencode "chat_id=$admin" --data-urlencode "text=$1" \
      "$API/bot$token/sendMessage" || true
  fi
}

if [ -z "${BACKUP_PASSWORD:-}" ] && [ -n "${BACKUP_PASSWORD_FILE:-}" ]; then
  if [ ! -s "$BACKUP_PASSWORD_FILE" ]; then
    echo "Нет файла с паролем $BACKUP_PASSWORD_FILE (или он пустой)" >&2
    notify "⚠️ Резервная копия не сделана: нет файла с паролем $BACKUP_PASSWORD_FILE"
    exit 1
  fi
  BACKUP_PASSWORD="$(head -n 1 "$BACKUP_PASSWORD_FILE" | tr -d '\r')"
  if [ -z "$BACKUP_PASSWORD" ]; then
    echo "В файле $BACKUP_PASSWORD_FILE пустой пароль" >&2
    notify "⚠️ Резервная копия не сделана: в файле $BACKUP_PASSWORD_FILE пустой пароль"
    exit 1
  fi
fi
if [ -z "${BACKUP_PASSWORD:-}" ]; then
  echo "Придумайте пароль для архива. Он понадобится при восстановлении — запишите его: без пароля архив не открыть."
  while :; do
    read -rsp "Пароль: " p1; echo
    read -rsp "Ещё раз: " p2; echo
    if [ -z "$p1" ]; then echo "Пустой пароль не подойдёт."; elif [ "$p1" != "$p2" ]; then echo "Не совпало, ещё раз."; else break; fi
  done
  BACKUP_PASSWORD="$p1"
fi
export BACKUP_PASSWORD

stamp="$(date +%Y%m%d-%H%M)"
backup_dir="${BACKUP_DIR:-$HOME}"
out="$backup_dir/yamusicbot-backup-$stamp.tar.gz.enc"

# Claude Code: переписка, память, настройки. Файл входа (.credentials.json) не копируем — на новом сервере
# вход заново; кэши и телеметрию тоже. В архиве папка называется claude-home/, restore.sh вернёт её в ~/.claude.
extra=()
stage="$(mktemp -d)"
if [ "${BACKUP_CLAUDE:-1}" != 0 ] && [ -d "$HOME/.claude" ]; then
  extra+=(-C "$HOME" --exclude=.claude/.credentials.json --exclude=.claude/cache --exclude=.claude/telemetry
          --exclude=.claude/shell-snapshots --exclude=.claude/session-env --exclude=.claude/downloads
          --transform='s#^\.claude#claude-home#' .claude)
fi
if command -v crontab >/dev/null && crontab -l >"$stage/crontab.txt" 2>/dev/null && [ -s "$stage/crontab.txt" ]; then
  extra+=(-C "$stage" crontab.txt)
fi

# База SQLite должна копироваться целой: на время копирования бот стоит (обычно несколько секунд).
restart=0
if command -v docker >/dev/null && docker compose ps --status running --services 2>/dev/null | grep -qx bot; then
  echo "Останавливаю бота на время копирования…"
  docker compose stop bot >/dev/null
  restart=1
fi
start_again() {
  rm -rf "$stage"
  if [ "$restart" = 1 ]; then
    echo "Запускаю бота обратно…"
    docker compose start bot >/dev/null || echo "Не удалось запустить бота: docker compose up -d" >&2
  fi
}
trap start_again EXIT

umask 077  # в архиве токены — только для владельца
# Код 1 у tar — «файл менялся во время чтения»: переписку Claude пишет сама идущая сессия, это не ошибка.
{ tar --warning=no-file-changed -czf - "${items[@]}" "${extra[@]}" || [ $? -eq 1 ]; } | openssl enc -e "${CIPHER[@]}" -salt -pass env:BACKUP_PASSWORD -out "$out"
start_again
trap - EXIT

# Проверка: архив расшифровывается этим паролем и в нём всё на месте.
listing="$(openssl enc -d "${CIPHER[@]}" -pass env:BACKUP_PASSWORD -in "$out" | tar -tzf -)"
for need in .env data/bot.db; do
  # Через <<<, а не echo | grep -q: grep выходит на первом совпадении, и на длинном списке echo падал бы от SIGPIPE.
  grep -qx "\\(\\./\\)\\?$need" <<<"$listing" || { echo "В архиве нет $need — что-то пошло не так" >&2; exit 1; }
done
size=$(wc -c <"$out")
echo
echo "✅ Копия готова: $out ($(( (size + 1023) / 1024 )) КБ), файлов: $(echo "$listing" | grep -vc '/$')"

# Старые копии в папке не копим: оставляем последние BACKUP_KEEP.
keep="${BACKUP_KEEP:-7}"
find "$backup_dir" -maxdepth 1 -name 'yamusicbot-backup-*.tar.gz.enc' -printf '%f\n' | sort -r | tail -n +"$((keep + 1))" |
  while read -r old; do rm -f "$backup_dir/$old"; done

token="$(env_value BOT_TOKEN)"
admin="$(env_value ADMIN_IDS | cut -d, -f1)"
file_id=""
if [ -n "$token" ] && [ -n "$admin" ] && [ "$size" -lt 49000000 ]; then
  echo "Отправляю копию вам в Telegram…"
  resp="$(curl -sS -m 300 -F chat_id="$admin" -F document=@"$out" \
    -F caption="Резервная копия бота $stamp. Пароль — тот, что вы придумали. Восстановление: scripts/restore.sh" \
    "$API/bot$token/sendDocument" || true)"
  file_id="$(printf '%s' "$resp" | python3 -c 'import json,sys; print(json.load(sys.stdin)["result"]["document"]["file_id"])' 2>/dev/null || true)"
  if [ -n "$file_id" ]; then
    # Команда восстановления — отдельным сообщением: терминал после переустановки сервера пропадёт, а чат останется.
    curl -sS -m 30 -o /dev/null --data-urlencode "chat_id=$admin" --data-urlencode "parse_mode=HTML" \
      --data-urlencode "text=♻️ Восстановить эту копию на новом сервере (после git clone бота):
<code>cd ~/yamusicbot &amp;&amp; bash scripts/restore.sh $file_id</code>
Спросит токен бота и пароль архива." "$API/bot$token/sendMessage" || true
    echo "✅ Копия у вас в Telegram, в чате с ботом, вместе с командой для восстановления."
  else
    echo "⚠️ В Telegram отправить не получилось — скачайте файл сами (команда ниже)."
    notify "⚠️ Резервная копия $stamp сделана, но в Telegram не отправилась. Она лежит на сервере: $out"
  fi
else
  echo "В Telegram не отправляю: нет ADMIN_IDS в .env или файл больше 49 МБ — скачайте его сами (команда ниже)."
  notify "⚠️ Резервная копия $stamp больше 49 МБ — в Telegram не влезла. Она лежит на сервере: $out"
fi

cat <<EOF

Скачать копию на свой компьютер (выполнить на компьютере, не на сервере):
  scp root@$(hostname -I 2>/dev/null | awk '{print $1}'):$out .

Восстановление на новом сервере — поставьте Docker и git, склонируйте бота, затем:
  cd ~/yamusicbot && bash scripts/restore.sh${file_id:+ $file_id}
EOF
if [ -n "$file_id" ]; then
  echo "(скрипт скачает архив из Telegram — спросит токен бота и пароль архива)"
fi
