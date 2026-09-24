# Клиент fxTunnel: публикует мини-приложение по HTTPS без открытых портов (например, если сервер за NAT).
# Та же база, что у бота, — отдельно ничего скачивать не нужно.
FROM python:3.12-slim
ARG TARGETARCH
COPY install.py /tmp/install.py
RUN python /tmp/install.py && rm /tmp/install.py
COPY entrypoint.sh /entrypoint.sh
RUN chmod 755 /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
