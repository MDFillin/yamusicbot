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
import contextlib
import contextvars
import logging
import random
import time
from collections import deque
from collections.abc import Callable, Iterator
from typing import Any
from urllib.parse import urlsplit

import aiohttp
from aiohttp_socks import ProxyConnectionError, ProxyConnector, ProxyError, ProxyTimeoutError

log = logging.getLogger(__name__)

CONNECT_ATTEMPTS = 3  # больше — это уже долбёжка, от которой Яндекс и режет IP
CONNECT_TIMEOUT = 4  # сек на одну попытку (TCP + TLS)
RETRY_PAUSE = 1.0  # сек: пауза перед повтором — 1–2 с, потом 2–3 с (не пачкой)
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
                await asyncio.sleep(RETRY_PAUSE * (attempt + random.random()))  # noqa: S311
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


# ---------- бережное обращение с Яндексом ----------
# Яндекс ограничивает IP целиком: однажды сервер бота так уже перестал его видеть (соединения до
# api.music.yandex.net терялись, а до других сайтов — нет). Поэтому темп запросов общий на весь бот — люди,
# фоновые задачи, скачивание, плеер, Ynison — и бот сам сбавляет его, как только Яндекс начинает отказывать.

API_RATE = 3.0  # запросов в секунду в среднем (YANDEX_RPS)
API_BURST = 10  # столько можно сразу, потом — по темпу
BACKGROUND_GAP = 2.0  # сек между фоновыми запросами; фон берёт только запас сверх половины — остальное людям
COOL_FACTOR = 0.3  # во время «остывания» темп для людей — меньше трети обычного, фон ждёт
MAX_WAIT = 30  # сек: дольше человеку ждать очереди не стоит — пусть попробует позже
FAILURE_SHARE = 0.4  # доля неудач среди последних запросов, после которой бот сбавляет темп
FAILURE_WINDOW = 10  # …считая не меньше стольких запросов
FIRST_COOLING = 60  # сек; после каждого следующего срыва — вдвое дольше
MAX_COOLING = 30 * 60
CALM_RELEASE = 30 * 60  # сек без срывов — и срок «остывания» снова короче
_background: contextvars.ContextVar[bool] = contextvars.ContextVar("yandex_background", default=False)


class YandexBusy(ConnectionError):
    """Запрос не дождался очереди к Яндексу: бот сейчас бережёт его лимиты."""


@contextlib.contextmanager
def background() -> Iterator[None]:
    """Запросы к Яндексу внутри — фоновые: реже и уступают людям (статистика, Ynison, перестановка треков)."""
    token = _background.set(True)
    try:
        yield
    finally:
        _background.reset(token)


class Guard:
    """Темп запросов к Яндексу («ведро с жетонами») и «остывание», когда Яндекс начинает отказывать:
    ответ 429 или заметная доля таймаутов и обрывов. Во время остывания фон ждёт, людям — медленнее."""

    clock: Callable[[], float] = staticmethod(time.monotonic)  # тесты подменяют часы
    sleep: Callable[[float], Any] = staticmethod(asyncio.sleep)

    def __init__(self, rate: float = API_RATE, burst: int = API_BURST) -> None:
        self.rate, self.burst = rate, burst
        # (причина, на сколько секунд, серьёзно ли) — сообщить владельцам (задаёт bot/main.py)
        self.on_trip: Callable[[str, int, bool], None] | None = None
        self.reset()

    def reset(self) -> None:
        now = self.clock()
        self._tokens, self._stamp, self._next_background = float(self.burst), now, 0.0
        self.cooling_until, self.level, self._calm_since = 0.0, 0, now
        self._recent: deque[bool] = deque(maxlen=30)
        self._minutes: deque[tuple[int, int]] = deque(maxlen=60)  # (минута, запросов) — для админ-панели
        self.totals = {"requests": 0, "background": 0, "waited": 0, "busy": 0, "throttled": 0, "failed": 0,
                       "trips": 0}
        self.last_trip: tuple[float, str] | None = None

    def configure(self, rate: float | None) -> None:
        if rate:
            self.rate = rate
            self.burst = max(API_BURST, int(rate * 3))
            self._tokens = min(self._tokens, self.burst)

    @property
    def cooling(self) -> bool:
        return self.clock() < self.cooling_until

    def _rate(self, now: float) -> float:
        return self.rate * (COOL_FACTOR if now < self.cooling_until else 1)

    async def acquire(self) -> None:
        """Дождаться своей очереди. Фон ждёт сколько нужно, человек — не дольше MAX_WAIT (потом YandexBusy)."""
        is_background = _background.get()
        started = self.clock()
        waited = False
        while True:
            now = self.clock()
            rate = self._rate(now)
            self._tokens = min(self.burst, self._tokens + (now - self._stamp) * rate)
            self._stamp = now
            if not is_background:
                delay = max(0.0, (1 - self._tokens) / rate)
                if delay and now - started + delay > MAX_WAIT:
                    self.totals["busy"] += 1
                    raise YandexBusy("слишком много запросов к Яндексу сразу — бот бережёт его лимиты, "
                                     "попробуйте через минуту")
            elif now < self.cooling_until:
                delay = min(self.cooling_until - now, 30.0)
            else:
                delay = max(self._next_background - now, (self.burst / 2 - self._tokens) / rate, 0.0)
            if delay < 0.001:  # доли миллисекунды не ждём: иначе из-за округления можно крутиться впустую
                self._tokens -= 1
                if is_background:
                    self._next_background = now + BACKGROUND_GAP
                    self.totals["background"] += 1
                self.totals["waited"] += waited
                self._count()
                return
            waited = True
            await self.sleep(delay)

    def _count(self) -> None:
        self.totals["requests"] += 1
        minute = int(time.time() // 60)
        if self._minutes and self._minutes[-1][0] == minute:
            self._minutes[-1] = (minute, self._minutes[-1][1] + 1)
        else:
            self._minutes.append((minute, 1))

    def report(self, ok: bool, throttled: bool = False, retry_after: float | None = None) -> None:
        """Итог запроса: по нему видно, начал ли Яндекс отказывать."""
        now = self.clock()
        if throttled:
            self.totals["throttled"] += 1
            if self.cooling:  # остальные запросы той же пачки: срок продлеваем, но не удваиваем
                self.cooling_until = max(self.cooling_until, now + min(retry_after or 0, MAX_COOLING))
            else:
                self._trip(retry_after or 0, "Яндекс ответил «слишком много запросов» (429)", throttled=True)
            return
        if not ok:
            self.totals["failed"] += 1
        self._recent.append(ok)
        failures = self._recent.count(False)
        if len(self._recent) >= FAILURE_WINDOW and failures / len(self._recent) >= FAILURE_SHARE:
            if not self.cooling:
                self._trip(0, f"{failures} из {len(self._recent)} последних запросов — таймауты и обрывы")
        elif ok and self.level and not self.cooling and now - self._calm_since > CALM_RELEASE:
            self.level -= 1  # полчаса спокойно — следующее остывание снова короче
            self._calm_since = now

    def _trip(self, at_least: float, reason: str, throttled: bool = False) -> None:
        now = self.clock()
        seconds = int(min(max(at_least, FIRST_COOLING * 2 ** self.level), MAX_COOLING))
        self.cooling_until = now + seconds
        self.level = min(self.level + 1, 5)
        self._calm_since = self.cooling_until
        self._recent.clear()
        self.totals["trips"] += 1
        self.last_trip = (time.time(), reason)
        log.warning("Яндекс: %s — сбавляю темп на %s с", reason, seconds)
        if self.on_trip is not None:
            try:  # серьёзно — 429 или срыв не первый подряд; одна короткая заминка сети — нет
                self.on_trip(reason, seconds, throttled or self.level >= 2)
            except Exception:
                log.exception("Не удалось сообщить о сбавленном темпе")

    def stats(self) -> dict[str, Any]:
        minute = int(time.time() // 60)
        return {
            "rate": self.rate,
            "per_min": round(sum(n for m, n in self._minutes if m > minute - 5) / 5, 1),
            "last_hour": sum(n for m, n in self._minutes if m > minute - 60),
            "cooling": max(0, int(self.cooling_until - self.clock())), "level": self.level,
            "last_trip": {"at": int(self.last_trip[0]), "reason": self.last_trip[1]} if self.last_trip else None,
            **self.totals,
        }


guard = Guard()


def _guard_trace() -> aiohttp.TraceConfig:
    """Каждый запрос сессий Яндекса — через очередь guard, а его итог — в учёт.

    Ожидание очереди идёт до начала запроса, поэтому в его сроки не засчитывается."""
    trace = aiohttp.TraceConfig()

    async def on_start(session: Any, ctx: Any, params: Any) -> None:
        await guard.acquire()

    async def on_end(session: Any, ctx: Any, params: Any) -> None:
        status = params.response.status
        retry_after = params.response.headers.get("Retry-After", "")
        guard.report(ok=status < 500, throttled=status == 429,
                     retry_after=float(retry_after) if retry_after.isdigit() else None)

    async def on_exception(session: Any, ctx: Any, params: Any) -> None:
        if isinstance(params.exception, (aiohttp.ClientConnectionError, TimeoutError, *PROXY_ERRORS)):
            guard.report(ok=False)

    trace.on_request_start.append(on_start)
    trace.on_request_end.append(on_end)
    trace.on_request_exception.append(on_exception)
    return trace


def timeout(total: float | None, read: float | None = None) -> aiohttp.ClientTimeout:
    """Общий срок на запрос; на установку соединения — отдельный короткий срок на каждую попытку."""
    connect = PROXY_CONNECT_TIMEOUT if _yandex_proxy else CONNECT_TIMEOUT
    return aiohttp.ClientTimeout(total=total, connect=None, sock_connect=connect, sock_read=read)


def _pool(kwargs: dict[str, Any]) -> dict[str, Any]:
    return {"limit": kwargs.pop("limit", 100), "limit_per_host": kwargs.pop("limit_per_host", 0),
            "keepalive_timeout": kwargs.pop("keepalive_timeout", 60)}


def session(**kwargs: Any) -> aiohttp.ClientSession:
    """Сессия напрямую (для всего, кроме Яндекса)."""
    return aiohttp.ClientSession(connector=RetryingConnector(**_pool(kwargs)), **kwargs)


def yandex_session(**kwargs: Any) -> aiohttp.ClientSession:
    """Сессия для Яндекса: через YANDEX_PROXY, если он задан, иначе напрямую; все запросы — через guard."""
    kwargs.setdefault("trace_configs", [_guard_trace()])
    if not _yandex_proxy:
        return session(**kwargs)
    kwargs["trust_env"] = False  # прокси из переменных окружения поверх нашего — только запутает
    return aiohttp.ClientSession(connector=RetryingProxyConnector(_yandex_proxy, **_pool(kwargs)), **kwargs)
