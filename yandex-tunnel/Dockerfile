# SSH-туннель до второго сервера: SOCKS5-прокси на порту 1080 внутри сети docker compose (наружу не открыт).
# Бот ходит через него к Яндексу (YANDEX_PROXY=socks5://yandex-tunnel:1080). См. README, «Прокси для Яндекса».
FROM alpine:3.20
RUN apk add --no-cache openssh-client autossh
COPY entrypoint.sh /entrypoint.sh
RUN chmod 755 /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
