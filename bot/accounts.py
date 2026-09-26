"""Аккаунты Яндекс Музыки пользователей бота.

Каждый подключает свой Яндекс сам: бот показывает код, человек вводит его на странице Яндекса
(как при входе на телевизоре) и подтверждает вход. Токены хранятся в базе, клиенты — в памяти.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field

from yandex_music.exceptions import DeviceAuthError, NetworkError, TimedOutError, YandexMusicError

from bot.errors import log_failure
from bot.storage import Storage
from bot.ym import YandexMusic, explain_start_error, make_client

log = logging.getLogger(__name__)

DEVICE_NAME = "Telegram Music Bot"  # так бот будет называться в списке устройств Яндекс ID
MIN_LOGIN_INTERVAL = 10  # сек: не выпрашивать у Яндекса новые коды слишком часто
MIN_POLL_INTERVAL = 1  # сек: чаще Яндекс о подтверждении входа не спрашиваем


class LoginError(RuntimeError):
    """Вход не удался; текст — объяснение для человека."""


def _explain_device_error(e: Exception) -> str:
    text = str(e)
    if "expired" in text:
        return "Код истёк — запросите новый"
    if "denied" in text:
        return "Вход отклонён на странице Яндекса"
    return f"Яндекс не подтвердил вход: {text}"


def _consume_exception(future: asyncio.Future) -> None:
    if not future.cancelled():
        future.exception()  # чтобы asyncio не ругался на «необработанную» ошибку, если результат никто не ждал


@dataclass
class LoginSession:
    """Вход по коду: код показываем человеку, в фоне ждём подтверждения на странице Яндекса."""

    code: str
    url: str
    expires_at: float
    result: asyncio.Future = field(default_factory=lambda: asyncio.get_running_loop().create_future())
    task: asyncio.Task | None = None

    def __post_init__(self) -> None:
        self.result.add_done_callback(_consume_exception)

    @property
    def status(self) -> str:
        if not self.result.done():
            return "pending"
        if self.result.cancelled():
            return "cancelled"
        return "failed" if self.result.exception() else "done"

    @property
    def error(self) -> str | None:
        return str(self.result.exception()) if self.status == "failed" else None

    @property
    def expires_in(self) -> int:
        return max(0, round(self.expires_at - time.monotonic()))

    async def wait(self) -> YandexMusic:
        """Подключённый аккаунт; LoginError — если вход не удался, CancelledError — если его отменили."""
        return await asyncio.shield(self.result)


class Accounts:
    def __init__(
        self,
        store: Storage,
        *,
        max_bitrate: int = 320,
        factory: Callable[[str], YandexMusic] | None = None,
        oauth: object | None = None,
    ) -> None:
        self._store = store
        self._factory = factory or (lambda token: YandexMusic(token, max_bitrate=max_bitrate))
        self._oauth = oauth  # request_device_code / poll_device_token; по умолчанию — клиент yandex-music
        self._clients: dict[int, YandexMusic] = {}
        self._logins: dict[int, LoginSession] = {}
        self._login_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._code_times: dict[int, float] = {}

    # ---------- подключённые аккаунты ----------

    @property
    def loaded(self) -> int:
        """Сколько клиентов Яндекса сейчас в памяти (для админ-панели)."""
        return len(self._clients)

    def is_connected(self, user_id: int) -> bool:
        return user_id in self._clients or self._store.get_account(user_id) is not None

    def client(self, user_id: int) -> YandexMusic | None:
        """Клиент пользователя (без подключения к Яндексу) или None, если он не входил."""
        ym = self._clients.get(user_id)
        if ym is None:
            account = self._store.get_account(user_id)
            if account is None:
                return None
            ym = self._clients[user_id] = self._factory(account[0])
        return ym

    async def get(self, user_id: int) -> YandexMusic | None:
        """Готовый к работе клиент; None — Яндекс не подключён; YandexNotReady — подключиться не удалось."""
        ym = self.client(user_id)
        if ym is None:
            return None
        if not ym.ready:
            await ym.ensure_started()
            self._store.set_login(user_id, ym.login)
        return ym

    async def reset(self, user_id: int) -> None:
        """Забывает клиент в памяти (токен остаётся): при следующем запросе подключимся заново и объясним ошибку."""
        ym = self._clients.pop(user_id, None)
        if ym is not None:
            await ym.close()

    async def add(self, user_id: int, token: str) -> YandexMusic:
        """Проверяет токен и привязывает аккаунт к пользователю (прежний, если был, заменяется)."""
        ym = self._factory(token)
        try:
            await ym.start()
        except Exception as e:
            await ym.close()
            log_failure(log, "Не удалось подключить аккаунт пользователя %s", user_id, exc=e)
            raise LoginError(explain_start_error(e)) from e
        self._store.set_account(user_id, token, ym.login)
        old = self._clients.get(user_id)
        self._clients[user_id] = ym
        if old is not None and old is not ym:
            await old.close()
        log.info("Пользователь %s подключил Яндекс Музыку (%s)", user_id, ym.login)
        return ym

    async def logout(self, user_id: int) -> bool:
        """Забывает аккаунт пользователя: токен удаляется из базы."""
        self.cancel_login(user_id)
        ym = self._clients.pop(user_id, None)
        if ym is not None:
            await ym.close()
        deleted = self._store.delete_account(user_id)
        if deleted:
            log.info("Пользователь %s отключил Яндекс Музыку", user_id)
        return deleted

    def import_legacy_token(self, token: str | None, users: frozenset[int]) -> list[int]:
        """Прежняя версия работала с одним токеном YANDEX_MUSIC_TOKEN для ALLOWED_USERS — отдаём его им.

        Делается один раз на токен: если потом человек отключит аккаунт, токен сам не вернётся.
        """
        if not token or not users:
            return []
        marker = hashlib.sha256(token.encode()).hexdigest()
        if self._store.get_meta("legacy_token") == marker:
            return []
        imported = [u for u in sorted(users) if self._store.get_account(u) is None]
        for user_id in imported:
            self._store.set_account(user_id, token, None)
        self._store.set_meta("legacy_token", marker)
        return imported

    # ---------- вход по коду ----------

    def login_session(self, user_id: int) -> LoginSession | None:
        return self._logins.get(user_id)

    async def start_login(self, user_id: int) -> LoginSession:
        """Новый код для входа или тот же, если прежний ещё действует (бот и приложение показывают один код)."""
        async with self._login_locks[user_id]:
            session = self._logins.get(user_id)
            if session is not None and session.status == "pending":
                return session
            if time.monotonic() - self._code_times.get(user_id, -MIN_LOGIN_INTERVAL) < MIN_LOGIN_INTERVAL:
                raise LoginError("Подождите несколько секунд и попробуйте снова")
            self._code_times[user_id] = time.monotonic()
            oauth = self._oauth or make_client()
            try:
                code = await oauth.request_device_code(device_name=DEVICE_NAME)
            except YandexMusicError as e:
                raise LoginError(f"Яндекс не выдал код для входа: {e}") from e
            session = LoginSession(code.user_code, code.verification_url, time.monotonic() + code.expires_in)
            session.task = asyncio.create_task(
                self._wait_confirmation(user_id, session, oauth, code.device_code, code.interval)
            )
            self._logins[user_id] = session
            return session

    def cancel_login(self, user_id: int) -> None:
        session = self._logins.pop(user_id, None)
        if session is None:
            return
        # Результат отменяем сразу: задача могла ещё не начаться, и тогда её except не сработает.
        session.result.cancel()
        if session.task is not None and not session.task.done():
            session.task.cancel()

    async def _wait_confirmation(
        self, user_id: int, session: LoginSession, oauth, device_code: str, interval: int
    ) -> None:
        try:
            while time.monotonic() < session.expires_at:
                await asyncio.sleep(max(interval, MIN_POLL_INTERVAL))
                try:
                    token = await oauth.poll_device_token(device_code)
                except DeviceAuthError as e:
                    raise LoginError(_explain_device_error(e)) from e
                except (NetworkError, TimedOutError):
                    continue  # сеть моргнула — спросим ещё раз
                if token is not None:
                    ym = await self.add(user_id, token.access_token)
                    if not session.result.done():
                        session.result.set_result(ym)
                    return
            raise LoginError("Код истёк — запросите новый")
        except asyncio.CancelledError:
            session.result.cancel()
            raise
        except LoginError as e:
            if not session.result.done():
                session.result.set_exception(e)
        except Exception as e:
            log.exception("Ошибка входа пользователя %s", user_id)
            if not session.result.done():
                session.result.set_exception(LoginError(f"Не удалось войти: {type(e).__name__}: {e}"))

    async def close(self) -> None:
        for user_id in list(self._logins):
            self.cancel_login(user_id)
        for ym in self._clients.values():
            await ym.close()
        self._clients.clear()
