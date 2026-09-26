"""Соединения с Яндексом: через прокси (YANDEX_PROXY) и так, чтобы пережить плохой маршрут.

Бывает, что у хостера часть путей до Яндекса сломана: новое соединение то устанавливается за доли секунды,
то не устанавливается вовсе (пакеты теряются). Каждое новое соединение уходит с нового порта и может пойти
другим путём, поэтому:
- соединение устанавливается с нескольких попыток, на каждую — короткий срок. Пока соединение не установлено,
  запрос ещё не отправлен, так что повторять безопасно для любого запроса;
- установленные соединения переиспользуются (keep-alive): раз нашёлся рабочий путь, по нему и работаем.

Если Яндекс с сервера бота совсем не открывается или сильно тормозит, весь трафик к нему (API, скачивание,
загрузка, плеер, вход, Ynison) можно пустить через прокси: YANDEX_PROXY=socks5://… или http://… — например,
через второй сервер по SSH (сервис yandex-tunnel в docker-compose.yml). Telegram при этом ходит как раньше.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import urlsplit

import aiohttp
from aiohttp_socks import ProxyConnectionError, ProxyConnector, ProxyError, ProxyTimeoutError

log = logging.getLogger(__name__)

CONNECT_ATTEMPTS = 6
CONNECT_TIMEOUT = 4  # сек на одну попытку (TCP + TLS)
PROXY_CONNECT_TIMEOUT = 15  # через прокси — дольше: соединение до Яндекса устанавливает уже он
PROXY_ERRORS = (ProxyError, ProxyConnectionError, ProxyTimeoutError)
_TRANSIENT = (TimeoutError, aiohttp.ClientConnectorError, *PROXY_ERRORS)

_yandex_proxy: str | None = None


def configure(yandex_proxy: str | None) -> None:
    """Задаётся при запуске бота (из .env). Действует на сессии, созданные после этого."""
    global _yandex_proxy
    _yandex_proxy = yandex_proxy or None
    if _yandex_proxy:
        log.info("Яндекс — через прокси %s", mask(_yandex_proxy))


def yandex_proxy() -> str | None:
    return _yandex_proxy


def mask(url: str | None) -> str | None:
    """Адрес прокси без логина и пароля — для логов и админ-панели."""
    if not url:
        return None
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.hostname}:{parts.port}" if parts.hostname else "прокси"


class _Retrying:
    """Устанавливает соединение с нескольких попыток (подмешивается к коннектору aiohttp)."""

    _attempts = CONNECT_ATTEMPTS

    async def _create_connection(self, req: Any, traces: Any, timeout: aiohttp.ClientTimeout) -> Any:
        for attempt in range(1, self._attempts + 1):
            try:
                proto = await super()._create_connection(req, traces, timeout)  # type: ignore[misc]
            except aiohttp.ClientConnectorCertificateError:
                raise  # чужой сертификат — это не сбой маршрута, повтор не поможет
            except _TRANSIENT as e:
                if attempt == self._attempts:
                    log.warning("Не удалось соединиться с %s за %s попыток: %r", req.host, attempt, e)
                    raise
                await asyncio.sleep(min(0.25 * attempt, 1.0))
                continue
            if attempt > 1:
                log.info("Соединение с %s установлено с %s-й попытки", req.host, attempt)
            return proto
        raise AssertionError("недостижимо")


class RetryingConnector(_Retrying, aiohttp.TCPConnector):
    def __init__(self, *args: Any, attempts: int = CONNECT_ATTEMPTS, **kwargs: Any) -> None:
        kwargs.setdefault("keepalive_timeout", 60)
        super().__init__(*args, **kwargs)
        self._attempts = attempts


class RetryingProxyConnector(_Retrying, ProxyConnector):
    """Через прокси; имена хостов разрешает сам прокси (Яндекс отдаёт адреса по его расположению)."""

    def __init__(self, url: str, attempts: int = CONNECT_ATTEMPTS, **kwargs: Any) -> None:
        from python_socks import ProxyType, parse_proxy_url

        proxy_type, host, port, username, password = parse_proxy_url(url)
        kwargs.setdefault("keepalive_timeout", 60)
        super().__init__(host=host, port=port, proxy_type=proxy_type, username=username, password=password,
                         rdns=proxy_type != ProxyType.HTTP, **kwargs)
        self._attempts = attempts


def timeout(total: float | None, read: float | None = None) -> aiohttp.ClientTimeout:
    """Общий срок на запрос; на установку соединения — отдельный короткий срок на каждую попытку."""
    connect = PROXY_CONNECT_TIMEOUT if _yandex_proxy else CONNECT_TIMEOUT
    return aiohttp.ClientTimeout(total=total, connect=None, sock_connect=connect, sock_read=read)


def session(**kwargs: Any) -> aiohttp.ClientSession:
    """Сессия напрямую (для всего, кроме Яндекса)."""
    connector = RetryingConnector(limit=kwargs.pop("limit", 100), limit_per_host=kwargs.pop("limit_per_host", 0))
    return aiohttp.ClientSession(connector=connector, **kwargs)


def yandex_session(**kwargs: Any) -> aiohttp.ClientSession:
    """Сессия для Яндекса: через YANDEX_PROXY, если он задан, иначе напрямую."""
    if not _yandex_proxy:
        return session(**kwargs)
    kwargs["trust_env"] = False  # прокси из переменных окружения поверх нашего — только запутает
    connector = RetryingProxyConnector(_yandex_proxy, limit=kwargs.pop("limit", 100),
                                       limit_per_host=kwargs.pop("limit_per_host", 0))
    return aiohttp.ClientSession(connector=connector, **kwargs)
