#!/bin/sh
# Запуск клиента fxTunnel с токеном из FXTUNNEL_TOKEN.
# Команда `fxtunnel http` берёт токен только из флага --token (переменные окружения она не читает),
# поэтому передаём его здесь. Сам токен в лог не пишем.
set -eu

fail() {
    echo "fxTunnel: $1" >&2
    echo "Токен: личный кабинет https://fxtun.ru → «Токены» → создать. Он показывается один раз," >&2
    echo "выглядит как sk_fxtunnel_ и 48 символов 0-9a-f. Впиши его в .env строкой FXTUNNEL_TOKEN=..." >&2
    echo "и выполни: docker compose up -d --force-recreate tunnel" >&2
    exit 1
}

token="${FXTUNNEL_TOKEN:-}"
[ -n "$token" ] || fail "не задан FXTUNNEL_TOKEN в .env."
case "$token" in
    sk_*) ;;
    *) fail "FXTUNNEL_TOKEN должен начинаться с sk_ (сейчас там что-то другое)." ;;
esac
case "$token" in
    *[!A-Za-z0-9_-]*) fail "в FXTUNNEL_TOKEN лишние символы (пробел, кавычки, кириллица или «…»): похоже на заготовку или обрезанную копию." ;;
esac
if [ "${#token}" -ne 60 ]; then
    echo "fxTunnel: FXTUNNEL_TOKEN длиной ${#token} символов, обычно 60 — проверь, что скопирован целиком." >&2
fi

exec fxtunnel "$@" --token "$token"
