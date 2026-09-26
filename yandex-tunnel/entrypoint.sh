#!/bin/sh
# Держит SSH-соединение со вторым сервером и поднимает на нём SOCKS5-прокси (-D). autossh переподключает
# туннель, если связь оборвалась; ServerAlive* замечают «зависшее» соединение за минуту.
set -eu
if [ -z "${YANDEX_SSH:-}" ]; then
  echo "Задайте в .env YANDEX_SSH=пользователь@адрес второго сервера (README, «Прокси для Яндекса»)" >&2
  exit 1
fi
KEY=/keys/id_ed25519
if [ ! -f "$KEY" ]; then
  echo "Нет ключа $KEY. Создайте: ssh-keygen -t ed25519 -N '' -f ~/yamusicbot/ssh/id_ed25519" >&2
  echo "и добавьте его на второй сервер: ssh-copy-id -i ~/yamusicbot/ssh/id_ed25519.pub ${YANDEX_SSH}" >&2
  exit 1
fi
echo "Туннель до ${YANDEX_SSH} (порт ${YANDEX_SSH_PORT:-22}), SOCKS5 на :1080"
# Без этого autossh сдаётся, если самое первое подключение не удалось (второй сервер ещё не поднялся).
export AUTOSSH_GATETIME=0
exec autossh -M 0 -N -D 0.0.0.0:1080 \
  -o ServerAliveInterval=20 -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes \
  -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/keys/known_hosts \
  -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=15 \
  -i "$KEY" -p "${YANDEX_SSH_PORT:-22}" "$YANDEX_SSH"
