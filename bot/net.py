"""Соединения, переживающие плохой маршрут до сервера.

Бывает, что у хостера часть путей до Яндекса сломана: новое соединение то устанавливается за доли секунды,
то не устанавливается вовсе (пакеты теряются). Каждое новое соединение уходит с нового порта и может пойти
другим путём, поэтому:
- соединение устанавливается с нескольких попыток, на каждую — короткий срок. Пока соединение не установлено,
  запрос ещё не отправлен, так что повторять безопасно для любого запроса;
- установленные соединения переиспользуются (keep-alive): раз нашёлся рабочий путь, по нему и работаем.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

log = logging.getLogger(__name__)

CONNECT_ATTEMPTS = 6
CONNECT_TIMEOUT = 4  # сек на одну попытку (TCP + TLS)


class RetryingConnector(aiohttp.TCPConnector):
    """TCPConnector, который устанавливает соединение с нескольких попыток."""

    def __init__(self, *args: Any, attempts: int = CONNECT_ATTEMPTS, **kwargs: Any) -> None:
        kwargs.setdefault("keepalive_timeout", 60)
        super().__init__(*args, **kwargs)
        self._attempts = attempts

    async def _create_connection(self, req: Any, traces: Any, timeout: aiohttp.ClientTimeout) -> Any:
        for attempt in range(1, self._attempts + 1):
            try:
                proto = await super()._create_connection(req, traces, timeout)
            except aiohttp.ClientConnectorCertificateError:
                raise  # чужой сертификат — это не сбой маршрута, повтор не поможет
            except (TimeoutError, aiohttp.ClientConnectorError) as e:
                if attempt == self._attempts:
                    log.warning("Не удалось соединиться с %s за %s попыток: %r", req.host, attempt, e)
                    raise
                await asyncio.sleep(min(0.25 * attempt, 1.0))
                continue
            if attempt > 1:
                log.info("Соединение с %s установлено с %s-й попытки", req.host, attempt)
            return proto
        raise AssertionError("недостижимо")


def timeout(total: float | None, read: float | None = None) -> aiohttp.ClientTimeout:
    """Общий срок на запрос; на установку соединения — CONNECT_TIMEOUT на каждую попытку."""
    return aiohttp.ClientTimeout(total=total, connect=None, sock_connect=CONNECT_TIMEOUT, sock_read=read)


def session(**kwargs: Any) -> aiohttp.ClientSession:
    connector = RetryingConnector(limit=kwargs.pop("limit", 100), limit_per_host=kwargs.pop("limit_per_host", 0))
    return aiohttp.ClientSession(connector=connector, **kwargs)
