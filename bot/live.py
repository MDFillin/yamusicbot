"""Прослушивания в реальном времени: каждое — с повторами и точным временем.

У каждого, кто включил статистику, бот держит соединение с Ynison (bot/ynison.py) и по кадрам состояния
плеера понимает, что играет, когда трек сменился или начался заново и сколько его на самом деле слушали.
Прослушивание засчитывается, как в Last.fm: трек слушали не меньше половины или 4 минуты (что меньше);
пропущенный раньше — не считается, но запоминается, чтобы история Яндекса не выдала его за прослушанный.

История Яндекса (bot/listening.py) остаётся запасным источником: если бот был выключен или трек играл
на устройстве без Ynison, прослушивание добирается оттуда (один раз за день, без повторов).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import aiohttp

from bot import ynison
from bot.admin import DAY_OFFSET, Admin
from bot.errors import log_failure, spawn
from bot.storage import Storage
from bot.ynison import Snapshot, YnisonAuthError, YnisonProtocolError

if TYPE_CHECKING:
    from bot.accounts import Accounts
    from bot.listening import Listening

log = logging.getLogger(__name__)

MIN_PLAY_MS = 30_000
MAX_NEEDED_MS = 240_000
RESTART_NEAR_START_MS = 15_000  # трек начался заново, если позиция вернулась к началу…
RESTART_AFTER_MS = 15_000  # …после того как его слушали хотя бы столько (иначе это перемотка)
LOOP_INTERVAL = 30  # сек: проверка подключений, засчитывание идущих треков, данные новых треков
AUTH_COOLDOWN = 3600  # вход отозван — повторить не раньше чем через час
RETRY_MIN, RETRY_MAX = 5, 300
STABLE_SESSION = 120  # соединение прожило столько — ошибки до него забываем, пауза снова минимальная
RAW_KEEP = 60_000  # символов последнего кадра — для проверки владельцем


def needed_ms(duration_ms: int) -> int:
    """Сколько нужно слушать, чтобы засчитать: половину трека, но не больше 4 минут."""
    if duration_ms <= 0:
        return MIN_PLAY_MS
    if duration_ms < MIN_PLAY_MS:
        return int(duration_ms * 0.8)  # совсем короткие треки — почти целиком
    return min(duration_ms // 2, MAX_NEEDED_MS)


def msk_day(ts: float) -> str:
    return (datetime.fromtimestamp(ts, UTC) + timedelta(seconds=DAY_OFFSET)).date().isoformat()


@dataclass
class Current:
    """Трек, который сейчас играет (одно прослушивание: повтор — уже новый Current)."""

    track_id: str
    album_id: str | None
    context: str
    context_type: str
    duration_ms: int
    started: float  # unix-время начала (для дня и порядка)
    pos_ms: float
    playing: bool
    speed: float
    anchor: float  # monotonic-время, к которому относятся pos_ms и listened_ms
    listened_ms: float = 0.0
    play_id: int | None = None  # строка в plays, когда засчитан


class PlayTracker:
    """Кадры состояния плеера одного пользователя → прослушивания в базе."""

    def __init__(self, store: Storage, user_id: int, source: str = "live") -> None:
        self.store, self.user_id, self.source = store, user_id, source
        self.cur: Current | None = None
        self.new_tracks: set[str] = set()  # впервые встреченные треки и источники — им нужны данные из Яндекса
        self.new_contexts: dict[str, str] = {}
        self.counted = 0
        self._status: str | None = None  # версия статуса из прошлого кадра

    def reconnected(self) -> None:
        """Новое соединение: первый кадр снова считаем по отметкам времени из самого кадра."""
        self._status = None

    def on_snapshot(self, snap: Snapshot, now: float | None = None, wall: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        wall = time.time() if wall is None else wall
        self._advance(now)
        cur = self.cur
        if snap.track_id is None:
            self._status = snap.status_key
            self.finish(now)
            return
        first, fresh = self._status is None, snap.status_key != self._status
        self._status = snap.status_key
        if first:  # когда статус выставлен, знаем только по часам устройства — досчитываем от них
            pos, playing = snap.position(), not snap.paused and not snap.stale()
        else:  # новый статус приходит сразу, как устройство его отправило: позиция — на этот момент
            pos, playing = snap.clamp(snap.progress_ms), not snap.paused
        if cur is not None and cur.track_id == snap.track_id and not fresh:
            cur.duration_ms = snap.duration_ms or cur.duration_ms  # в кадре поменялось что-то другое
            self._check()
            return
        restarted = (cur is not None and cur.track_id == snap.track_id and pos < RESTART_NEAR_START_MS
                     and pos + 5_000 < cur.pos_ms and (cur.listened_ms >= RESTART_AFTER_MS or cur.play_id))
        if cur is None or cur.track_id != snap.track_id or restarted:
            fresh = cur is None  # первый кадр после запуска бота или включения статистики
            self.finish(now)
            cur = self.cur = Current(
                track_id=snap.track_id, album_id=snap.album_id, context=snap.context,
                context_type=snap.context_type, duration_ms=snap.duration_ms, started=wall, pos_ms=pos,
                playing=playing, speed=snap.speed, anchor=now)
            if fresh and pos >= RESTART_NEAR_START_MS:
                # Застали трек на середине — возможно, его уже засчитали до перезапуска бота: продолжаем то же
                # прослушивание, а не начинаем второе (длинный трек иначе засчитался бы дважды).
                found = self.store.ongoing_play(self.user_id, cur.track_id, int(wall - pos / 1000) - 120)
                if found is not None:
                    cur.play_id, started, listened = found
                    cur.started, cur.listened_ms = float(started), float(listened)
            self.store.mark_seen(self.user_id, cur.track_id, msk_day(wall))
            self.new_tracks.add(cur.track_id)
            if cur.context != "none":
                self.new_contexts[cur.context] = cur.context_type
        else:
            cur.pos_ms = pos
            cur.playing = playing
            cur.speed = snap.speed
            cur.duration_ms = snap.duration_ms or cur.duration_ms
        self._check()

    def tick(self, now: float | None = None) -> None:
        """Между кадрами трек играет дальше: засчитать его, как только дослушали до порога."""
        self._advance(time.monotonic() if now is None else now)
        self._check()

    def finish(self, now: float | None = None) -> None:
        """Трек закончился (сменился, начался заново, очередь пуста): записать, сколько его слушали."""
        cur = self.cur
        if cur is None:
            return
        self._advance(time.monotonic() if now is None else now)
        self._check()
        if cur.play_id is not None:
            self.store.update_play_ms(cur.play_id, int(cur.listened_ms))
        self.cur = None

    def _advance(self, now: float) -> None:
        cur = self.cur
        if cur is None:
            return
        if cur.playing:
            gain = max(0.0, now - cur.anchor) * 1000 * cur.speed
            if cur.duration_ms:  # дальше конца трек не играет, даже если устройство пропало, не сказав «пауза»
                gain = min(gain, max(0.0, cur.duration_ms - cur.pos_ms))
            cur.pos_ms += gain
            cur.listened_ms += gain
        cur.anchor = now

    def _check(self) -> None:
        cur = self.cur
        if cur is None or cur.play_id is not None or cur.listened_ms < needed_ms(cur.duration_ms):
            return
        cur.play_id = self.store.add_play(self.user_id, int(cur.started), msk_day(cur.started), cur.track_id,
                                          cur.context, int(cur.listened_ms), self.source)
        self.counted += 1


@dataclass
class Connection:
    user_id: int
    token: str
    tracker: PlayTracker
    task: asyncio.Task | None = None
    connected: bool = False
    since: float | None = None  # unix-время текущего подключения
    last_frame: float | None = None
    last_error: str | None = None
    raw: str | None = None  # последний кадр (только для админов — проверка разбора)


class LiveTracker:
    """Соединения с Ynison для всех, кто включил статистику: поднимает, переподключает, закрывает."""

    def __init__(self, store: Storage, accounts: Accounts, admin: Admin, listening: Listening) -> None:
        self.store, self.accounts, self.admin, self.listening = store, accounts, admin, listening
        self.conns: dict[int, Connection] = {}
        self._cooldown: dict[int, float] = {}
        self._http: aiohttp.ClientSession | None = None
        self._task: asyncio.Task | None = None
        self._jobs: set[asyncio.Task] = set()

    @property
    def enabled(self) -> bool:
        return self.admin.settings.live_tracking

    # ---------- жизненный цикл ----------

    def start(self) -> None:
        self._task = spawn(self._run(), "прослушивания в реальном времени")

    async def close(self) -> None:
        tasks = [self._task, *(c.task for c in self.conns.values()), *self._jobs]
        for task in tasks:
            if task is not None and not task.done():
                task.cancel()
        for task in tasks:
            if task is not None:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        for conn in self.conns.values():
            conn.tracker.finish()
        self.conns.clear()
        if self._http is not None:
            await self._http.close()

    async def _run(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log_failure(log, "Ynison: сбой фонового цикла", exc=e)
            await asyncio.sleep(LOOP_INTERVAL)

    def wanted(self) -> dict[int, str]:
        """{пользователь: токен} — у кого должно быть соединение прямо сейчас."""
        if not self.enabled:
            return {}
        result = {}
        now = time.monotonic()
        for user_id in self.store.stats_users():
            if self._cooldown.get(user_id, 0) > now or self.admin.check(user_id) is not None:
                continue
            account = self.store.get_account(user_id)
            if account is not None:
                result[user_id] = account[0]
        return result

    async def tick(self) -> None:
        wanted = self.wanted()
        for user_id, conn in list(self.conns.items()):
            if wanted.get(user_id) != conn.token or conn.task is None or conn.task.done():
                await self._stop(user_id)  # выключил статистику, сменил аккаунт, заблокирован или соединение умерло
        for user_id, token in wanted.items():
            if user_id not in self.conns:
                self._begin(user_id, token)
        for conn in list(self.conns.values()):
            conn.tracker.tick()
            await self._fill(conn)

    def _begin(self, user_id: int, token: str) -> None:
        conn = self.conns[user_id] = Connection(user_id, token, PlayTracker(self.store, user_id))
        conn.task = spawn(self._keep(conn), f"Ynison {user_id}", self._jobs)

    async def _stop(self, user_id: int) -> None:
        conn = self.conns.pop(user_id, None)
        if conn is None:
            return
        if conn.task is not None and not conn.task.done():
            conn.task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await conn.task
        conn.tracker.finish()

    def wake(self, user_id: int) -> None:
        """Статистику только что включили — подключиться сразу, не дожидаясь цикла."""
        if not self.enabled or user_id in self.conns or self.admin.check(user_id) is not None:
            return
        account = self.store.get_account(user_id)
        if account is not None:
            self._cooldown.pop(user_id, None)
            self._begin(user_id, account[0])

    def forget(self, user_id: int) -> None:
        """Статистику выключили или удалили: закрыть соединение сразу, не дожидаясь цикла."""
        self._cooldown.pop(user_id, None)
        spawn(self._stop(user_id), f"Ynison стоп {user_id}", self._jobs)

    def device_id(self, user_id: int) -> str:
        """Постоянный id устройства: иначе у пользователя копились бы «новые устройства» при каждом подключении."""
        device = self.store.get_setting(user_id, "ynison_device")
        if not device:
            device = secrets.token_hex(8)
            self.store.set_setting(user_id, "ynison_device", device)
        return device

    # ---------- одно соединение ----------

    async def _keep(self, conn: Connection) -> None:
        delay = RETRY_MIN
        while True:
            started = time.monotonic()
            try:
                await self._session(conn)
                conn.last_error = "соединение закрыто сервером"
            except asyncio.CancelledError:
                raise
            except YnisonAuthError as e:
                conn.last_error = f"вход не принят: {e}"
                log.warning("Ynison %s: Яндекс не принял вход (%s) — повторю через час", conn.user_id, e)
                self._cooldown[conn.user_id] = time.monotonic() + AUTH_COOLDOWN
                return
            except YnisonProtocolError as e:
                conn.last_error = str(e)[:500]
                await self.admin.live_health.failed(conn.user_id, str(e)[:400])
            except (TimeoutError, aiohttp.ClientError, OSError, ConnectionError) as e:
                conn.last_error = f"{type(e).__name__}: {e}"[:500]
                log.info("Ynison %s: соединение прервалось: %r", conn.user_id, e)
            except Exception as e:
                conn.last_error = f"{type(e).__name__}: {e}"[:500]
                log_failure(log, "Ynison %s: непредвиденная ошибка", conn.user_id, exc=e)
            finally:
                conn.connected = False
            if time.monotonic() - started > STABLE_SESSION:
                delay = RETRY_MIN
            await asyncio.sleep(delay + random.uniform(0, delay / 2))  # noqa: S311 — разнести переподключения
            delay = min(delay * 2, RETRY_MAX)

    async def _session(self, conn: Connection) -> None:
        if self._http is None or self._http.closed:
            # Соединений столько, сколько людей со статистикой: без лимита пула и без общего таймаута
            # (живость проверяют пинги websocket).
            self._http = aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=0),
                                               timeout=aiohttp.ClientTimeout(total=None, sock_connect=20))
        first = True
        keep_raw = self.admin.is_admin(conn.user_id)
        conn.tracker.reconnected()

        async def on_frame(frame: dict, raw: str) -> None:
            nonlocal first
            if keep_raw:
                conn.raw = raw[:RAW_KEEP]
            snap = ynison.parse_state(frame)
            if snap is None:
                if first:  # в ответ на подключение сервер всегда присылает состояние плеера
                    raise YnisonProtocolError(f"первый кадр без состояния плеера: {raw[:300]}")
                return
            conn.last_frame = time.time()
            if first:
                first = False
                conn.connected, conn.since, conn.last_error = True, time.time(), None
                await self.admin.live_health.succeeded()
            conn.tracker.on_snapshot(snap)

        await ynison.listen(self._http, conn.token, self.device_id(conn.user_id), on_frame)

    async def _fill(self, conn: Connection) -> None:
        """Данные новых треков и названия источников — из аккаунта пользователя, пачкой раз в цикл."""
        tracker = conn.tracker
        if not tracker.new_tracks and not tracker.new_contexts:
            return
        tracks, contexts = sorted(tracker.new_tracks), dict(tracker.new_contexts)
        tracker.new_tracks.clear()
        tracker.new_contexts.clear()
        try:
            ym = await self.accounts.get(conn.user_id)
        except Exception as e:  # Яндекс недоступен — попробуем со следующей порцией
            log.info("Ynison %s: не удалось получить данные треков: %r", conn.user_id, e)
            tracker.new_tracks.update(tracks)
            tracker.new_contexts.update(contexts)
            return
        if ym is not None:
            await self.listening.fill_meta(ym, tracks)
            await self.listening.fill_contexts(ym, contexts)

    # ---------- для админ-панели ----------

    def status(self) -> dict[str, Any]:
        connected = sum(1 for c in self.conns.values() if c.connected)
        return {"enabled": self.enabled, "connections": len(self.conns), "connected": connected,
                "health": self.admin.live_health.json()}

    def user_status(self, user_id: int) -> dict[str, Any]:
        conn = self.conns.get(user_id)
        cur = conn.tracker.cur if conn else None
        return {
            "enabled": self.enabled,
            "stats_enabled": self.listening.enabled(user_id),
            "connected": bool(conn and conn.connected),
            "since": conn.since if conn else None,
            "last_frame": conn.last_frame if conn else None,
            "last_error": conn.last_error if conn else None,
            "cooldown": max(0, int(self._cooldown.get(user_id, 0) - time.monotonic())) or None,
            "now": {"track_id": cur.track_id, "context": cur.context, "pos_ms": int(cur.pos_ms),
                    "duration_ms": cur.duration_ms, "playing": cur.playing, "listened_ms": int(cur.listened_ms),
                    "needed_ms": needed_ms(cur.duration_ms), "counted": cur.play_id is not None} if cur else None,
            "raw": conn.raw if conn else None,
        }
