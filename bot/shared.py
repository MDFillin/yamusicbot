"""Общие плейлисты: один плейлист на несколько человек, каждый добавляет треки из своего приложения Яндекса.

Совместных плейлистов (соавторов) у Яндекса больше нет, поэтому у каждого участника — свой плейлист в своём
аккаунте, а бот держит их одинаковыми: раз в несколько минут смотрит, что каждый добавил и удалил у себя, и
повторяет это у остальных. Участники — пользователи бота (бот работает с их аккаунтами Яндекса), новых зовут
ссылкой-приглашением t.me/<бот>?start=join_<код>.

Как понять, что человек сделал сам: после каждой синхронизации запоминаем, что было в его плейлисте
(shared_seen). Есть сейчас, а в прошлый раз не было — добавил он; было, а теперь нет — удалил. Общий список
(shared_tracks) — то, что должно быть у всех. Новичок (или плейлист, который ещё ни разу не синхронизировали)
ничего не удаляет: всё его — добавляется в общий, а общий — к нему.

Осторожности:
- если чей-то плейлист не прочитался (нет связи, вышел из аккаунта), его изменения ждут следующего раза, а не
  считаются удалением всего;
- если кто-то разом удалил много (MASS_DELETE), это скорее случайность: у остальных не удаляем, а ему
  возвращаем и говорим об этом;
- свои загруженные аудиофайлы Яндекс даёт добавлять только владельцу: такие треки остаются у него, остальным
  не переносятся;
- каждое изменение — с ревизией плейлиста: если человек правит его в эту же секунду, Яндекс откажет, и бот
  повторит в следующий раз.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from aiogram.exceptions import TelegramAPIError

from bot import net
from bot.admin import display_name
from bot.errors import log_failure, spawn
from bot.storage import Storage
from bot.ym import YandexMusic, YandexNotReady

if TYPE_CHECKING:
    from aiogram import Bot

    from bot.accounts import Accounts

log = logging.getLogger(__name__)

SYNC_INTERVAL = 180  # сек: как часто сверять каждый общий плейлист
LOOP_INTERVAL = 60
MANUAL_INTERVAL = 20  # «Синхронизировать сейчас» — не чаще
MAX_MEMBERS = 10
MAX_PER_USER = 10  # общих плейлистов у одного человека
MASS_DELETE = (20, 0.3)  # разом удалено больше стольких и больше такой доли — не повторяем у остальных

Track = tuple[str, str | None]


class SharedError(RuntimeError):
    """Понятное человеку «нельзя»: полно участников, приглашение устарело и т. п."""


@dataclass
class SyncReport:
    added: int = 0  # новых треков в общем списке
    removed: int = 0
    members: int = 0  # скольких удалось синхронизировать
    errors: dict[int, str] = field(default_factory=dict)  # участник → что не так
    restored: dict[int, int] = field(default_factory=dict)  # участник → сколько вернули после массового удаления


@dataclass
class _Member:
    user_id: int
    kind: int
    ym: YandexMusic
    playlist: object
    current: list[Track]
    seen: set[str] | None  # None — ещё ни разу не синхронизировали


class SharedPlaylists:
    def __init__(self, store: Storage, accounts: Accounts, bot: Bot | None = None) -> None:
        self.store, self.accounts, self.bot = store, accounts, bot
        self._locks: dict[int, asyncio.Lock] = {}
        self._last: dict[int, float] = {}
        self._task: asyncio.Task | None = None
        self._jobs: set[asyncio.Task] = set()

    # ---------- жизненный цикл ----------

    def start(self) -> None:
        self._task = spawn(self._run(), "общие плейлисты")

    async def close(self) -> None:
        for task in [self._task, *self._jobs]:
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    async def _run(self) -> None:
        with net.background():  # запросы к Яндексу отсюда уступают людям
            while True:
                try:
                    await self.tick()
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    log_failure(log, "Общие плейлисты: сбой фонового цикла", exc=e)
                await asyncio.sleep(LOOP_INTERVAL)

    async def tick(self) -> None:
        for shared_id in self.store.shared_ids():
            if time.monotonic() - self._last.get(shared_id, -1e9) >= SYNC_INTERVAL:
                await self.sync(shared_id)

    def sync_soon(self, shared_id: int) -> None:
        async def run() -> None:
            with net.background():
                await self.sync(shared_id)

        spawn(run(), f"общий плейлист {shared_id}", self._jobs)

    def can_sync_now(self, shared_id: int) -> bool:
        return time.monotonic() - self._last.get(shared_id, -1e9) >= MANUAL_INTERVAL

    # ---------- создать, позвать, выйти ----------

    def create(self, user_id: int, title: str, kind: int) -> int:
        if len(self.store.shared_for_user(user_id)) >= MAX_PER_USER:
            raise SharedError(f"У вас уже {MAX_PER_USER} общих плейлистов — больше нельзя")
        shared_id = self.store.create_shared(title[:100], user_id, secrets.token_urlsafe(9))
        self.store.add_shared_member(shared_id, user_id, kind)
        return shared_id

    def join(self, code: str, user_id: int, kind: int) -> dict:
        shared = self.store.shared_by_code(code)
        if shared is None:
            raise SharedError("Приглашение недействительно: общий плейлист удалён")
        members = self.store.shared_members(shared["id"])
        if any(m["user_id"] == user_id for m in members):
            raise SharedError("Вы уже в этом общем плейлисте")
        if len(members) >= MAX_MEMBERS:
            raise SharedError(f"В общем плейлисте уже {MAX_MEMBERS} человек — больше нельзя")
        if len(self.store.shared_for_user(user_id)) >= MAX_PER_USER:
            raise SharedError(f"У вас уже {MAX_PER_USER} общих плейлистов — больше нельзя")
        self.store.add_shared_member(shared["id"], user_id, kind)
        return shared

    def leave(self, shared_id: int, user_id: int) -> None:
        """Выйти: ваш плейлист остаётся как есть, просто больше не синхронизируется. Последний вышел — удаляем."""
        shared = self.store.get_shared(shared_id)
        if shared is None:
            return
        self.store.remove_shared_member(shared_id, user_id)
        rest = self.store.shared_members(shared_id)
        if not rest:
            self.store.delete_shared(shared_id)
        elif shared["owner"] == user_id:
            self.store.set_shared_owner(shared_id, rest[0]["user_id"])

    # ---------- синхронизация ----------

    async def sync(self, shared_id: int) -> SyncReport | None:
        lock = self._locks.setdefault(shared_id, asyncio.Lock())
        async with lock:
            self._last[shared_id] = time.monotonic()
            if self.store.get_shared(shared_id) is None:
                return None
            report = SyncReport()
            members = await self._load(shared_id, report)
            if not members:
                return report
            canon = self.store.shared_tracks(shared_id)
            new_canon, removed = self._merge(canon, members, report)
            for m in members:
                await self._apply(shared_id, m, new_canon, removed, report)
            self.store.set_shared_tracks(shared_id, new_canon)
            self.store.set_shared_synced(shared_id)
            if report.added or report.removed:
                log.info("Общий плейлист %s: +%s −%s", shared_id, report.added, report.removed)
            for user_id, n in report.restored.items():
                await self._tell(user_id, f"🤝 Из общего плейлиста «{self.store.get_shared(shared_id)['title']}» "
                                          f"разом удалено {n} треков — похоже, случайно. У остальных я их не удалял "
                                          "и вернул вам. Если хотите убрать их у всех — удаляйте понемногу.")
            return report

    async def _load(self, shared_id: int, report: SyncReport) -> list[_Member]:
        members = []
        for row in self.store.shared_members(shared_id):
            user_id = row["user_id"]
            try:
                ym = await self.accounts.get(user_id)
                if ym is None:
                    raise YandexNotReady("Яндекс Музыка не подключена к боту")
                playlist = await ym.get_playlist(row["kind"])
            except asyncio.CancelledError:
                raise
            except Exception as e:  # нет связи, вышел из аккаунта — его изменения подождут
                report.errors[user_id] = str(e)[:200] or type(e).__name__
                self.store.set_shared_member_error(shared_id, user_id, report.errors[user_id])
                continue
            if playlist is None:
                report.errors[user_id] = "плейлист удалён — выйдите из общего плейлиста и присоединитесь заново"
                self.store.set_shared_member_error(shared_id, user_id, report.errors[user_id])
                continue
            try:
                current = await ym.track_refs(playlist.tracks or [])
            except asyncio.CancelledError:
                raise
            except Exception as e:
                report.errors[user_id] = str(e)[:200] or type(e).__name__
                self.store.set_shared_member_error(shared_id, user_id, report.errors[user_id])
                continue
            seen = self.store.shared_seen(shared_id, user_id) if row["synced"] else None
            members.append(_Member(user_id, row["kind"], ym, playlist, current, seen))
        return members

    @staticmethod
    def _merge(canon: list[tuple[str, str | None, int]], members: list[_Member],
               report: SyncReport) -> tuple[list[tuple[str, str | None, int]], set[str]]:
        """Новый общий список: прежний без удалённого кем-то и с добавленным кем-то (в конце)."""
        in_canon = {t for t, _, _ in canon}
        added: dict[str, tuple[str | None, int]] = {}
        removed: set[str] = set()
        for m in members:
            ids = [t for t, _ in m.current]
            if m.seen is None:
                gone: set[str] = set()
            else:
                gone = (m.seen - set(ids)) & in_canon
                if len(gone) > MASS_DELETE[0] and len(gone) > MASS_DELETE[1] * max(len(m.seen), 1):
                    report.restored[m.user_id] = len(gone)
                    gone = set()  # вернём ему — общий список их ещё содержит
            removed |= gone
            for track_id, album_id in m.current:
                fresh = track_id not in in_canon and (m.seen is None or track_id not in m.seen)
                if fresh and track_id not in added:
                    added[track_id] = (album_id, m.user_id)
        # Альбом, который раньше не узнали (в плейлисте Яндекса его нет в albumId), — из того, что есть сейчас.
        albums = {t: a for m in members for t, a in m.current if a}
        new_canon = [(t, a or albums.get(t), by) for t, a, by in canon if t not in removed]
        new_canon += [(t, a, by) for t, (a, by) in added.items()]
        report.added, report.removed = len(added), len(removed)
        return new_canon, removed

    async def _apply(self, shared_id: int, m: _Member, canon: list[tuple[str, str | None, int]], removed: set[str],
                     report: SyncReport) -> None:
        have = {t for t, _ in m.current}
        # Чужие загрузки добавить нельзя — у остальных их просто нет.
        to_add = [(t, a) for t, a, by in canon if t not in have and (a or by == m.user_id)]
        # Убрать всё, что было у него при прошлой синхронизации и чего в общем списке больше нет: удалённое сейчас
        # и то, что не получилось удалить в прошлый раз (тогда его снимок не обновился).
        in_canon = {t for t, _, _ in canon}
        to_remove = (have & removed) | ((have & m.seen) - in_canon if m.seen is not None else set())
        try:
            if to_add or to_remove:
                await m.ym.edit_playlist(m.kind, m.playlist, remove=to_remove, add=to_add)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # например, человек как раз правит плейлист — повторим в следующий раз
            report.errors[m.user_id] = str(e)[:200] or type(e).__name__
            self.store.set_shared_member_error(shared_id, m.user_id, report.errors[m.user_id])
            log.info("Общий плейлист %s: не удалось обновить у %s: %r", shared_id, m.user_id, e)
            return
        self.store.set_shared_seen(shared_id, m.user_id, (have - to_remove) | {t for t, _ in to_add})
        report.members += 1

    async def _tell(self, user_id: int, text: str) -> None:
        if self.bot is None:
            return
        with contextlib.suppress(TelegramAPIError):
            await self.bot.send_message(user_id, text)

    # ---------- для чата и приложения ----------

    def name(self, user_id: int) -> str:
        user = self.store.get_user(user_id)
        return display_name(user) if user else f"ID {user_id}"

    def describe(self, shared_id: int, user_id: int) -> dict | None:
        shared = self.store.get_shared(shared_id)
        members = self.store.shared_members(shared_id)
        me = next((m for m in members if m["user_id"] == user_id), None)
        if shared is None or me is None:
            return None
        return {
            "id": shared_id, "title": shared["title"], "code": shared["code"], "owner": shared["owner"] == user_id,
            "kind": me["kind"], "tracks": len(self.store.shared_tracks(shared_id)), "synced_at": shared["synced_at"],
            "error": me["error"],
            "members": [{"id": m["user_id"], "name": self.name(m["user_id"]),
                         "me": m["user_id"] == user_id, "error": m["error"]} for m in members],
        }
