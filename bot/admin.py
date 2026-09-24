"""Админ-панель владельца: пользователи, блокировки, техработы, лимиты, рассылка, журнал, ошибки и бэкап.

Админы задаются только в .env (ADMIN_IDS) — из самой панели добавить админа нельзя. Кто админ, бот узнаёт
по Telegram ID: в чате его подставляет Telegram, в мини-приложении — подписанный Telegram initData.
"""

from __future__ import annotations

import asyncio
import calendar
import contextlib
import html
import json
import logging
import os
import platform
import shutil
import subprocess
import tempfile
import time
import traceback
from collections import deque
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

import aiogram
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import FSInputFile

from bot.accounts import Accounts
from bot.config import Config
from bot.storage import Storage

log = logging.getLogger(__name__)

DAY_OFFSET = 3 * 3600  # сутки для статистики и лимитов считаем по Москве
ADMIN_MAX_AGE = 3600  # сек: админ-API принимает только недавно открытое приложение
TOUCH_INTERVAL = 60  # сек: как часто обновлять «был в сети» одного пользователя
DENY_NOTICE_INTERVAL = 60  # сек: как часто напоминать заблокированному, что доступа нет
BROADCAST_RATE = 20  # сообщений в секунду (Telegram разрешает около 30)
AUDIENCES = {"all": "всем", "connected": "с подключённым Яндексом"}

AUDIT_LABELS = {
    "ban": "заблокировал", "unban": "разблокировал", "disconnect": "отключил Яндекс у",
    "reset_limits": "сбросил лимиты", "message": "написал", "broadcast": "запустил рассылку",
    "broadcast_cancel": "остановил рассылку", "broadcast_done": "рассылка завершена", "settings": "настройки",
    "backup": "бэкап базы", "denied": "🚨 попытка открыть админку",
}

REASONS = {
    "banned": "⛔ Доступ к боту для вас закрыт.",
    "maintenance": "🛠 Бот на техническом обслуживании. Загляните чуть позже.",
    "closed": "🚪 Бот сейчас не принимает новых пользователей.",
}


def today(now: float | None = None) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime((now or time.time()) + DAY_OFFSET))


def day_start(day: str) -> int:
    """Unix-время начала суток day (по Москве)."""
    return calendar.timegm(time.strptime(day, "%Y-%m-%d")) - DAY_OFFSET


def display_name(user: dict) -> str:
    name = " ".join(x for x in (user.get("first_name"), user.get("last_name")) if x) or f"ID {user['id']}"
    return f"{name} (@{user['username']})" if user.get("username") else name


class LimitReached(RuntimeError):
    pass


class Denied(Exception):
    def __init__(self, reason: str, text: str) -> None:
        super().__init__(text)
        self.reason = reason


@dataclass
class BotSettings:
    maintenance: bool = False
    maintenance_text: str = ""
    closed_since: int | None = None  # с этого момента новые пользователи не допускаются
    download_limit: int = 0  # треков в сутки на человека, 0 — без ограничений
    upload_limit: int = 0

    @classmethod
    def load(cls, raw: str | None) -> BotSettings:
        try:
            data = json.loads(raw or "{}")
        except ValueError:
            data = {}
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def dump(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)


class ErrorLog(logging.Handler):
    """Последние предупреждения и ошибки из лога — чтобы найти запись по коду из сообщения пользователю."""

    def __init__(self, size: int = 300) -> None:
        super().__init__(logging.WARNING)
        self.records: deque[dict[str, Any]] = deque(maxlen=size)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
            if record.exc_info and record.exc_info[1] is not None:
                lines = traceback.format_exception(*record.exc_info)
                message += "\n" + "".join(lines)[-3000:]
            self.records.append({"at": int(record.created), "level": record.levelname, "logger": record.name,
                                 "message": message[:4000]})
        except Exception:  # лог не должен ронять бота
            self.handleError(record)

    def recent(self, query: str = "", limit: int = 100) -> list[dict[str, Any]]:
        q = query.strip().lower()
        items = [r for r in reversed(self.records) if not q or q in r["message"].lower()]
        return items[:limit]

    def count_since(self, ts: float, level: str = "ERROR") -> int:
        return sum(1 for r in self.records if r["at"] >= ts and r["level"] in (level, "CRITICAL"))


class Broadcaster:
    """Рассылка сообщения всем пользователям в фоне, с соблюдением лимитов Telegram."""

    def __init__(self, admin: Admin) -> None:
        self._admin = admin
        self._task: asyncio.Task | None = None
        self._cancel = False
        self.status: dict[str, Any] = {"state": "idle"}

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self, actor: int, text: str, audience: str) -> dict[str, Any]:
        if self.running:
            raise RuntimeError("Рассылка уже идёт — дождитесь её окончания или остановите")
        if audience not in AUDIENCES:
            raise ValueError("Неизвестная аудитория")
        ids = self._admin.store.user_ids(audience)
        self._cancel = False
        self.status = {"state": "running", "audience": audience, "total": len(ids), "sent": 0, "failed": 0,
                       "blocked": 0, "started_at": int(time.time()), "finished_at": None, "by": actor,
                       "preview": text[:200], "error": None}
        self._task = asyncio.create_task(self._run(actor, ids, text))
        return self.status

    def cancel(self) -> bool:
        if not self.running:
            return False
        self._cancel = True
        return True

    async def _run(self, actor: int, ids: list[int], text: str) -> None:
        bot, st = self._admin.bot, self.status
        try:
            for user_id in ids:
                if self._cancel:
                    st["state"] = "cancelled"
                    break
                await self._send_one(bot, user_id, text)
                await asyncio.sleep(1 / BROADCAST_RATE)
            else:
                st["state"] = "done"
        except TelegramBadRequest as e:  # например, ошибка в HTML-разметке — остальным отправлять бессмысленно
            st["state"], st["error"] = "failed", str(e)
        except Exception as e:
            log.exception("Рассылка прервалась")
            st["state"], st["error"] = "failed", type(e).__name__
        finally:
            st["finished_at"] = int(time.time())
            if st["state"] == "running":
                st["state"] = "cancelled"
            self._admin.audit(actor, "broadcast_done",
                              details=f"{st['state']}: {st['sent']} из {st['total']}, заблокировали {st['blocked']}")
            with contextlib.suppress(Exception):
                await bot.send_message(actor, self.summary())

    async def _send_one(self, bot: Bot, user_id: int, text: str) -> None:
        st = self.status
        for attempt in range(3):
            try:
                await bot.send_message(user_id, text, disable_web_page_preview=True)
                st["sent"] += 1
                return
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
            except TelegramForbiddenError:
                self._admin.mark_blocked(user_id)
                st["blocked"] += 1
                return
            except TelegramBadRequest as e:
                if "parse" in str(e).lower() or "entit" in str(e).lower():
                    raise
                st["failed"] += 1  # чат не найден и т. п.
                return
            except Exception:
                if attempt == 2:
                    st["failed"] += 1
                    log.warning("Рассылка: не удалось отправить %s", user_id, exc_info=True)
                    return
                await asyncio.sleep(1)
        st["failed"] += 1

    def summary(self) -> str:
        st = self.status
        titles = {"done": "✅ Рассылка завершена", "cancelled": "⏹ Рассылка остановлена",
                  "failed": "⚠️ Рассылка прервалась", "running": "📣 Рассылка идёт"}
        text = (f"{titles.get(st['state'], 'Рассылка')}\n"
                f"Доставлено: {st['sent']} из {st['total']}\n"
                f"Заблокировали бота: {st['blocked']} · Ошибок: {st['failed']}")
        if st.get("error"):
            text += f"\nПричина: {html.escape(st['error'])}"
        return text


class Admin:
    def __init__(self, config: Config, store: Storage, accounts: Accounts, bot: Bot) -> None:
        self.config = config
        self.store = store
        self.accounts = accounts
        self.bot = bot
        self.settings = BotSettings.load(store.get_meta("admin_settings"))
        self.errors = ErrorLog()
        self.broadcaster = Broadcaster(self)
        self.started_at = time.time()
        self.bot_username: str | None = None
        self.inline_enabled: bool | None = None  # включён ли инлайн-режим в @BotFather (узнаём при запуске)
        self._touched: dict[int, float] = {}
        self._denied: dict[int, float] = {}
        self._probes: dict[int, float] = {}
        self._ffmpeg_version: str | None = None

    # ---------- доступ ----------

    def is_admin(self, user_id: int | None) -> bool:
        return user_id is not None and user_id in self.config.admin_ids

    def touch(self, user: Any) -> None:
        """Отмечает, что пользователь был в боте (не чаще раза в минуту на человека)."""
        now = time.time()
        if now - self._touched.get(user.id, 0) < TOUCH_INTERVAL:
            return
        self._touched[user.id] = now
        if len(self._touched) > 50_000:
            self._touched.clear()
        self.store.touch_user(user.id, getattr(user, "first_name", None), getattr(user, "last_name", None),
                              getattr(user, "username", None), today(now), int(now))

    def mark_blocked(self, user_id: int) -> None:
        """Пользователь заблокировал бота; как только напишет снова, touch снимет отметку."""
        self.store.set_blocked_bot(user_id)
        self._touched.pop(user_id, None)

    def check(self, user_id: int) -> str | None:
        """Причина отказа в доступе (banned | maintenance | closed) или None."""
        if self.is_admin(user_id):
            return None
        if self.store.is_banned(user_id):
            return "banned"
        if self.settings.maintenance:
            return "maintenance"
        closed = self.settings.closed_since
        if closed is not None and self.store.get_account(user_id) is None:
            first = self.store.first_seen(user_id)
            if first is None or first >= closed:
                return "closed"
        return None

    def reason_text(self, reason: str) -> str:
        if reason == "maintenance" and self.settings.maintenance_text:
            return "🛠 " + self.settings.maintenance_text
        return REASONS[reason]

    def should_notify_denied(self, user_id: int) -> bool:
        """Сообщать заблокированному об отказе не чаще раза в минуту (иначе бот отвечает на каждое сообщение)."""
        now = time.time()
        if now - self._denied.get(user_id, 0) < DENY_NOTICE_INTERVAL:
            return False
        self._denied[user_id] = now
        return True

    async def probe(self, user: Any, where: str) -> None:
        """Чужой пытался открыть админку: пишем в журнал и сообщаем админам (не чаще раза в час на человека)."""
        now = time.time()
        if now - self._probes.get(user.id, 0) < 3600:
            return
        self._probes[user.id] = now
        name = display_name({"id": user.id, **{k: getattr(user, k, None) for k in ("first_name", "last_name",
                                                                                    "username")}})
        self.audit(user.id, "denied", user.id, where)
        log.warning("Попытка открыть админ-панель: %s (%s), %s", name, user.id, where)
        for admin_id in self.config.admin_ids:
            with contextlib.suppress(Exception):
                await self.bot.send_message(
                    admin_id, f"🚨 Кто-то пытался открыть админ-панель: <b>{html.escape(name)}</b> "
                              f"(<code>{user.id}</code>) — {html.escape(where)}. Доступа он не получил.")

    # ---------- лимиты ----------

    def check_limit(self, user_id: int, key: str) -> None:
        limit = self.settings.download_limit if key == "download" else self.settings.upload_limit
        if not limit or self.is_admin(user_id):
            return
        if self.store.get_count(user_id, today(), key) >= limit:
            what = "скачиваний" if key == "download" else "загрузок"
            raise LimitReached(f"Дневной лимит {what} ({limit}) исчерпан — он обновится в полночь по Москве")

    def count(self, user_id: int, key: str) -> None:
        self.store.add_count(user_id, today(), key)

    def left(self, user_id: int, key: str) -> int | None:
        limit = self.settings.download_limit if key == "download" else self.settings.upload_limit
        if not limit or self.is_admin(user_id):
            return None
        return max(0, limit - self.store.get_count(user_id, today(), key))

    # ---------- настройки бота ----------

    def update_settings(self, actor: int, changes: dict[str, Any]) -> BotSettings:
        s = self.settings
        details = []
        if "maintenance" in changes:
            s.maintenance = bool(changes["maintenance"])
            details.append(f"техработы {'вкл' if s.maintenance else 'выкл'}")
        if "maintenance_text" in changes:
            s.maintenance_text = str(changes["maintenance_text"] or "").strip()[:500]
            details.append("текст техработ")
        if "closed" in changes:
            closed = bool(changes["closed"])
            if closed != (s.closed_since is not None):
                s.closed_since = int(time.time()) if closed else None
                details.append("регистрация закрыта" if closed else "регистрация открыта")
        for key in ("download_limit", "upload_limit"):
            if key in changes:
                value = int(changes[key] or 0)
                if not 0 <= value <= 100_000:
                    raise ValueError("Лимит — от 0 (без ограничений) до 100000")
                setattr(s, key, value)
                details.append(f"{'скачиваний' if key == 'download_limit' else 'загрузок'} в сутки: {value or '∞'}")
        self.store.set_meta("admin_settings", s.dump())
        if details:
            self.audit(actor, "settings", details="; ".join(details))
        return s

    def settings_json(self) -> dict[str, Any]:
        s = self.settings
        return {"maintenance": s.maintenance, "maintenance_text": s.maintenance_text,
                "closed": s.closed_since is not None, "closed_since": s.closed_since,
                "download_limit": s.download_limit, "upload_limit": s.upload_limit}

    # ---------- действия с пользователями ----------

    def audit(self, actor: int, action: str, target: int | None = None, details: str | None = None) -> None:
        self.store.add_audit(actor, action, target, details)

    def audit_log(self, limit: int = 100) -> list[dict[str, Any]]:
        names: dict[int, str] = {}

        def name(user_id: int | None) -> str | None:
            if user_id is None:
                return None
            if user_id not in names:
                names[user_id] = display_name(self.store.get_user(user_id) or {"id": user_id})
            return names[user_id]

        return [{**e, "label": AUDIT_LABELS.get(e["action"], e["action"]), "actor_name": name(e["actor"]),
                 "target_name": name(e["target"])} for e in self.store.audit(limit)]

    def ban(self, actor: int, user_id: int, reason: str | None = None) -> None:
        if self.is_admin(user_id):
            raise ValueError("Админа заблокировать нельзя")
        reason = (reason or "").strip()[:200] or None
        self.store.set_banned(user_id, True, reason)
        self.audit(actor, "ban", user_id, reason)

    def unban(self, actor: int, user_id: int) -> None:
        self.store.set_banned(user_id, False)
        self.audit(actor, "unban", user_id)

    async def disconnect(self, actor: int, user_id: int) -> bool:
        done = await self.accounts.logout(user_id)
        self.audit(actor, "disconnect", user_id)
        return done

    def reset_limits(self, actor: int, user_id: int) -> None:
        self.store.reset_counts(user_id, today())
        self.audit(actor, "reset_limits", user_id)

    async def message(self, actor: int, user_id: int, text: str) -> None:
        text = text.strip()
        if not text:
            raise ValueError("Пустое сообщение")
        try:
            await self.bot.send_message(user_id, text, disable_web_page_preview=True)
        except TelegramForbiddenError as e:
            self.mark_blocked(user_id)
            raise ValueError("Пользователь заблокировал бота") from e
        except TelegramBadRequest as e:
            raise ValueError(f"Telegram не принял сообщение: {e.message}") from e
        self.audit(actor, "message", user_id, text[:200])

    def start_broadcast(self, actor: int, text: str, audience: str) -> dict[str, Any]:
        text = text.strip()
        if not text:
            raise ValueError("Пустое сообщение")
        if len(text) > 4000:
            raise ValueError("Слишком длинное сообщение: Telegram принимает до 4096 символов")
        status = self.broadcaster.start(actor, text, audience)
        self.audit(actor, "broadcast", details=f"{AUDIENCES[audience]} ({status['total']}): {text[:150]}")
        return status

    def cancel_broadcast(self, actor: int) -> bool:
        if self.broadcaster.cancel():
            self.audit(actor, "broadcast_cancel")
            return True
        return False

    async def backup(self, actor: int) -> int:
        """Отправляет копию базы админу в личный чат. Возвращает размер в байтах."""
        fd, name = tempfile.mkstemp(prefix="backup-", suffix=".db", dir=self.store.path.parent)
        os.close(fd)
        try:
            self.store.backup(Path(name))
            size = os.path.getsize(name)
            await self.bot.send_document(
                actor, FSInputFile(name, filename=f"yamusicbot-{today()}.db"),
                caption="💾 Резервная копия базы бота.\n⚠️ Внутри входы пользователей в Яндекс Музыку — "
                        "храните файл надёжно и никому не пересылайте.",
            )
        finally:
            with contextlib.suppress(OSError):
                os.unlink(name)
        self.audit(actor, "backup", details=f"{size // 1024} КБ")
        return size

    # ---------- статистика ----------

    def stats(self, days: int = 14) -> dict[str, Any]:
        now = time.time()
        t = today(now)
        series_days = [today(now - 86400 * i) for i in range(days - 1, -1, -1)]
        since = series_days[0]
        week = today(now - 86400 * 6)
        month = today(now - 86400 * 29)
        active = self.store.daily_active(since)
        new = self.store.new_users_by_day(day_start(since), DAY_OFFSET)
        downloads = self.store.daily_counts("download", since)
        uploads = self.store.daily_counts("upload", since)
        top = []
        for user_id, n in self.store.top_users("download", week):
            user = self.store.get_user(user_id) or {"id": user_id}
            top.append({"id": user_id, "name": display_name(user), "downloads": n})
        users = self.store.user_stats()
        users.update(
            new_today=self.store.new_users_since(day_start(t)),
            new_week=self.store.new_users_since(day_start(week)),
            active_today=active.get(t, 0),
            active_week=self.store.active_since(week),
            active_month=self.store.active_since(month),
        )
        return {
            "users": users,
            "downloads": {"today": downloads.get(t, 0), "total": self.store.total_count("download")},
            "uploads": {"today": uploads.get(t, 0), "total": self.store.total_count("upload")},
            "series": [{"day": d, "active": active.get(d, 0), "new": new.get(d, 0),
                        "downloads": downloads.get(d, 0), "uploads": uploads.get(d, 0)} for d in series_days],
            "top": top,
            "errors_24h": self.errors.count_since(now - 86400),
        }

    def ffmpeg_version(self) -> str | None:
        if self._ffmpeg_version is None and shutil.which("ffmpeg"):
            try:
                out = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=5).stdout
                words = out.split()  # «ffmpeg version 7.1.1-1 Copyright …»
                self._ffmpeg_version = words[2] if len(words) > 2 else "?"
            except (OSError, subprocess.SubprocessError):
                self._ffmpeg_version = "?"
        return self._ffmpeg_version

    def server_info(self) -> dict[str, Any]:
        data_dir = self.store.path.parent
        disk = shutil.disk_usage(data_dir)
        return {
            "uptime": int(time.time() - self.started_at),
            "python": platform.python_version(),
            "aiogram": aiogram.__version__,
            "system": platform.platform(terse=True),
            "ffmpeg": self.ffmpeg_version(),
            "memory_mb": _rss_mb(),
            "db_bytes": self.store.path.stat().st_size if self.store.path.exists() else 0,
            "disk_free": disk.free,
            "disk_total": disk.total,
            "clients": self.accounts.loaded,
            "bot_username": self.bot_username,
            "inline": self.inline_enabled,
            "webapp_url": self.config.webapp_url,
            "max_bitrate": self.config.max_bitrate,
            "web_max_upload_mb": self.config.web_max_upload_mb,
            "admins": sorted(self.config.admin_ids),
        }


def _rss_mb() -> float | None:
    try:
        with open("/proc/self/status", encoding="ascii") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return round(int(line.split()[1]) / 1024, 1)
    except OSError:
        pass
    try:
        import resource

        return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)
    except (ImportError, OSError):
        return None
