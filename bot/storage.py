"""Хранилище бота в SQLite: аккаунты Яндекса пользователей, их настройки, кэш file_id и данные для админ-панели."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from pathlib import Path

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    user_id INTEGER PRIMARY KEY,   -- Telegram ID
    token TEXT NOT NULL,           -- OAuth-токен Яндекс Музыки этого пользователя
    login TEXT,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS upload_targets (
    user_id INTEGER PRIMARY KEY,
    kind INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS file_ids (
    account INTEGER NOT NULL,      -- uid аккаунта Яндекса: без Плюса Яндекс отдаёт другой файл
    track_id TEXT NOT NULL,
    bitrate INTEGER NOT NULL,
    file_id TEXT NOT NULL,
    PRIMARY KEY (account, track_id, bitrate)
);
CREATE TABLE IF NOT EXISTS user_settings (
    user_id INTEGER NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (user_id, key)
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
-- Все, кто писал боту или открывал приложение (для админ-панели, рассылки и блокировок).
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    first_name TEXT,
    last_name TEXT,
    username TEXT,
    first_seen INTEGER NOT NULL,
    last_seen INTEGER NOT NULL,
    banned INTEGER NOT NULL DEFAULT 0,
    ban_reason TEXT,
    blocked_bot INTEGER NOT NULL DEFAULT 0  -- пользователь сам заблокировал бота (узнаём при отправке)
);
CREATE TABLE IF NOT EXISTS activity (
    user_id INTEGER NOT NULL,
    day TEXT NOT NULL,             -- ГГГГ-ММ-ДД: был активен в этот день
    PRIMARY KEY (user_id, day)
);
CREATE TABLE IF NOT EXISTS counters (
    user_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    key TEXT NOT NULL,             -- download | upload
    n INTEGER NOT NULL,
    PRIMARY KEY (user_id, day, key)
);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at INTEGER NOT NULL,
    actor INTEGER NOT NULL,        -- кто: админ (или чужой, если это попытка войти в админку)
    action TEXT NOT NULL,
    target INTEGER,
    details TEXT
);
CREATE INDEX IF NOT EXISTS users_last_seen ON users (last_seen);
CREATE INDEX IF NOT EXISTS activity_day ON activity (day);
CREATE INDEX IF NOT EXISTS counters_day ON counters (day, key);
"""


class Storage:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, isolation_level=None)  # autocommit: каждая запись сразу на диске
        # LOWER в SQLite понимает только латиницу, а искать людей нужно и по-русски.
        self._db.create_function("PYLOWER", 1, lambda v: v.lower() if isinstance(v, str) else v, deterministic=True)
        self._db.executescript(SCHEMA)
        try:
            os.chmod(path, 0o600)  # тут токены пользователей
        except OSError:
            pass
        self._import_json(path.with_name("storage.json"))
        self._backfill_users()

    def close(self) -> None:
        self._db.close()

    def _one(self, sql: str, *args) -> tuple | None:
        return self._db.execute(sql, args).fetchone()

    def _all(self, sql: str, *args) -> list[tuple]:
        return self._db.execute(sql, args).fetchall()

    def backup(self, dest: Path) -> None:
        """Целостная копия базы (можно делать на ходу)."""
        target = sqlite3.connect(dest)
        try:
            self._db.backup(target)
        finally:
            target.close()

    # ---------- аккаунты ----------

    def get_account(self, user_id: int) -> tuple[str, str | None] | None:
        """(токен, логин) или None, если пользователь не подключал Яндекс."""
        return self._one("SELECT token, login FROM accounts WHERE user_id = ?", user_id)

    def set_account(self, user_id: int, token: str, login: str | None) -> None:
        self._db.execute(
            "INSERT INTO accounts (user_id, token, login, created_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (user_id) DO UPDATE SET token = excluded.token, login = excluded.login",
            (user_id, token, login, int(time.time())),
        )
        now = int(time.time())
        self._db.execute("INSERT OR IGNORE INTO users (user_id, first_seen, last_seen) VALUES (?, ?, ?)",
                         (user_id, now, now))

    def set_login(self, user_id: int, login: str | None) -> None:
        self._db.execute("UPDATE accounts SET login = ? WHERE user_id = ?", (login, user_id))

    def delete_account(self, user_id: int) -> bool:
        deleted = self._db.execute("DELETE FROM accounts WHERE user_id = ?", (user_id,)).rowcount
        self._db.execute("DELETE FROM upload_targets WHERE user_id = ?", (user_id,))
        return bool(deleted)

    def account_count(self) -> int:
        return self._one("SELECT COUNT(*) FROM accounts")[0]

    # ---------- кэш file_id: один раз скачанный трек повторно отправляется мгновенно ----------

    def get_file_id(self, account: int, track_id: str, bitrate: int) -> str | None:
        row = self._one(
            "SELECT file_id FROM file_ids WHERE account = ? AND track_id = ? AND bitrate = ?",
            account, track_id, bitrate,
        )
        return row[0] if row else None

    def set_file_id(self, account: int, track_id: str, bitrate: int, file_id: str) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO file_ids (account, track_id, bitrate, file_id) VALUES (?, ?, ?, ?)",
            (account, track_id, bitrate, file_id),
        )

    # ---------- плейлист, куда сразу загружаются присланные файлы ----------

    def get_upload_target(self, user_id: int) -> int | None:
        row = self._one("SELECT kind FROM upload_targets WHERE user_id = ?", user_id)
        return row[0] if row else None

    def set_upload_target(self, user_id: int, kind: int | None) -> None:
        if kind is None:
            self._db.execute("DELETE FROM upload_targets WHERE user_id = ?", (user_id,))
        else:
            self._db.execute("INSERT OR REPLACE INTO upload_targets (user_id, kind) VALUES (?, ?)", (user_id, kind))

    # ---------- настройки пользователя (качество и т. п.) ----------

    def get_setting(self, user_id: int, key: str) -> str | None:
        row = self._one("SELECT value FROM user_settings WHERE user_id = ? AND key = ?", user_id, key)
        return row[0] if row else None

    def set_setting(self, user_id: int, key: str, value: str | None) -> None:
        if value is None:
            self._db.execute("DELETE FROM user_settings WHERE user_id = ? AND key = ?", (user_id, key))
        else:
            self._db.execute("INSERT OR REPLACE INTO user_settings (user_id, key, value) VALUES (?, ?, ?)",
                             (user_id, key, value))

    # ---------- пользователи (админ-панель) ----------

    def touch_user(self, user_id: int, first_name: str | None, last_name: str | None, username: str | None,
                   day: str, now: int | None = None) -> None:
        now = now or int(time.time())
        self._db.execute(
            "INSERT INTO users (user_id, first_name, last_name, username, first_seen, last_seen) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (user_id) DO UPDATE SET first_name = excluded.first_name, "
            "last_name = excluded.last_name, username = excluded.username, last_seen = excluded.last_seen, "
            "blocked_bot = 0",
            (user_id, first_name, last_name, username, now, now),
        )
        self._db.execute("INSERT OR IGNORE INTO activity (user_id, day) VALUES (?, ?)", (user_id, day))

    def get_user(self, user_id: int) -> dict | None:
        rows = self._users("WHERE u.user_id = ?", (user_id,), limit=1)
        return rows[0] if rows else None

    def list_users(self, query: str = "", status: str = "all", offset: int = 0, limit: int = 50) -> list[dict]:
        where, args = self._user_filter(query, status)
        return self._users(where + " ORDER BY u.last_seen DESC", args, limit, offset)

    def count_users(self, query: str = "", status: str = "all") -> int:
        where, args = self._user_filter(query, status)
        return self._one(f"SELECT COUNT(*) FROM users u LEFT JOIN accounts a ON a.user_id = u.user_id {where}",
                         *args)[0]

    @staticmethod
    def _user_filter(query: str, status: str) -> tuple[str, tuple]:
        conditions, args = [], []
        if query:
            q = query.strip().lstrip("@").lower()
            if q.lstrip("-").isdigit():
                conditions.append("u.user_id = ?")
                args.append(int(q))
            else:
                conditions.append("(PYLOWER(COALESCE(u.username, '')) LIKE ? ESCAPE '\\' OR "
                                  "PYLOWER(COALESCE(u.first_name, '') || ' ' || COALESCE(u.last_name, '')) "
                                  "LIKE ? ESCAPE '\\' OR PYLOWER(COALESCE(a.login, '')) LIKE ? ESCAPE '\\')")
                like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
                args += [like, like, like]
        conditions.append({
            "connected": "a.user_id IS NOT NULL",
            "banned": "u.banned = 1",
            "blocked": "u.blocked_bot = 1",
        }.get(status, "1 = 1"))
        return "WHERE " + " AND ".join(conditions), tuple(args)

    def _users(self, where: str, args: tuple, limit: int, offset: int = 0) -> list[dict]:
        rows = self._all(
            "SELECT u.user_id, u.first_name, u.last_name, u.username, u.first_seen, u.last_seen, u.banned, "
            "u.ban_reason, u.blocked_bot, a.login, a.created_at, "
            "(SELECT COALESCE(SUM(n), 0) FROM counters c WHERE c.user_id = u.user_id AND c.key = 'download'), "
            "(SELECT COALESCE(SUM(n), 0) FROM counters c WHERE c.user_id = u.user_id AND c.key = 'upload') "
            f"FROM users u LEFT JOIN accounts a ON a.user_id = u.user_id {where} LIMIT ? OFFSET ?",
            *args, limit, offset,
        )
        keys = ("id", "first_name", "last_name", "username", "first_seen", "last_seen", "banned", "ban_reason",
                "blocked_bot", "yandex_login", "connected_at", "downloads", "uploads")
        users = []
        for row in rows:
            user = dict(zip(keys, row, strict=True))
            user["banned"], user["blocked_bot"] = bool(user["banned"]), bool(user["blocked_bot"])
            user["connected"] = user["connected_at"] is not None
            users.append(user)
        return users

    def user_ids(self, status: str = "all") -> list[int]:
        where, args = self._user_filter("", status)
        return [r[0] for r in self._all(
            f"SELECT u.user_id FROM users u LEFT JOIN accounts a ON a.user_id = u.user_id {where} "
            "AND u.banned = 0 AND u.blocked_bot = 0 ORDER BY u.user_id", *args)]

    def set_banned(self, user_id: int, banned: bool, reason: str | None = None) -> None:
        now = int(time.time())
        self._db.execute(
            "INSERT INTO users (user_id, first_seen, last_seen, banned, ban_reason) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT (user_id) DO UPDATE SET banned = excluded.banned, ban_reason = excluded.ban_reason",
            (user_id, now, now, int(banned), reason if banned else None),
        )

    def is_banned(self, user_id: int) -> bool:
        row = self._one("SELECT banned FROM users WHERE user_id = ?", user_id)
        return bool(row and row[0])

    def first_seen(self, user_id: int) -> int | None:
        row = self._one("SELECT first_seen FROM users WHERE user_id = ?", user_id)
        return row[0] if row else None

    def set_blocked_bot(self, user_id: int) -> None:
        self._db.execute("UPDATE users SET blocked_bot = 1 WHERE user_id = ?", (user_id,))

    def _backfill_users(self) -> None:
        """Кто подключил Яндекс до появления таблицы users, тоже должен быть в списке пользователей."""
        self._db.execute(
            "INSERT OR IGNORE INTO users (user_id, first_seen, last_seen) "
            "SELECT user_id, created_at, created_at FROM accounts"
        )

    # ---------- счётчики и статистика ----------

    def add_count(self, user_id: int, day: str, key: str, n: int = 1) -> None:
        self._db.execute(
            "INSERT INTO counters (user_id, day, key, n) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (user_id, day, key) DO UPDATE SET n = n + excluded.n",
            (user_id, day, key, n),
        )

    def get_count(self, user_id: int, day: str, key: str) -> int:
        row = self._one("SELECT n FROM counters WHERE user_id = ? AND day = ? AND key = ?", user_id, day, key)
        return row[0] if row else 0

    def reset_counts(self, user_id: int, day: str) -> None:
        self._db.execute("DELETE FROM counters WHERE user_id = ? AND day = ?", (user_id, day))

    def daily_counts(self, key: str, since: str) -> dict[str, int]:
        return dict(self._all("SELECT day, SUM(n) FROM counters WHERE key = ? AND day >= ? GROUP BY day", key, since))

    def total_count(self, key: str) -> int:
        return self._one("SELECT COALESCE(SUM(n), 0) FROM counters WHERE key = ?", key)[0]

    def daily_active(self, since: str) -> dict[str, int]:
        return dict(self._all("SELECT day, COUNT(*) FROM activity WHERE day >= ? GROUP BY day", since))

    def active_since(self, since: str) -> int:
        return self._one("SELECT COUNT(DISTINCT user_id) FROM activity WHERE day >= ?", since)[0]

    def new_users_since(self, ts: int) -> int:
        return self._one("SELECT COUNT(*) FROM users WHERE first_seen >= ?", ts)[0]

    def new_users_by_day(self, since_ts: int, offset: int) -> dict[str, int]:
        """Новые пользователи по дням (offset — сдвиг часового пояса в секундах)."""
        return dict(self._all(
            "SELECT DATE(first_seen + ?, 'unixepoch'), COUNT(*) FROM users WHERE first_seen >= ? GROUP BY 1",
            offset, since_ts,
        ))

    def user_stats(self) -> dict[str, int]:
        total, banned, blocked = self._one(
            "SELECT COUNT(*), COALESCE(SUM(banned), 0), COALESCE(SUM(blocked_bot), 0) FROM users")
        return {"total": total, "banned": banned, "blocked": blocked, "connected": self.account_count()}

    def top_users(self, key: str, since: str, limit: int = 5) -> list[tuple[int, int]]:
        return self._all(
            "SELECT user_id, SUM(n) FROM counters WHERE key = ? AND day >= ? GROUP BY user_id "
            "ORDER BY 2 DESC LIMIT ?", key, since, limit)

    # ---------- журнал действий админа ----------

    def add_audit(self, actor: int, action: str, target: int | None = None, details: str | None = None) -> None:
        self._db.execute("INSERT INTO audit (at, actor, action, target, details) VALUES (?, ?, ?, ?, ?)",
                         (int(time.time()), actor, action, target, details))
        self._db.execute("DELETE FROM audit WHERE id <= (SELECT MAX(id) - 2000 FROM audit)")

    def audit(self, limit: int = 100) -> list[dict]:
        rows = self._all("SELECT at, actor, action, target, details FROM audit ORDER BY id DESC LIMIT ?", limit)
        return [dict(zip(("at", "actor", "action", "target", "details"), r, strict=True)) for r in rows]

    # ---------- служебное ----------

    def get_meta(self, key: str) -> str | None:
        row = self._one("SELECT value FROM meta WHERE key = ?", key)
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self._db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))

    def _import_json(self, legacy: Path) -> None:
        """Однократно переносит настройки из прежнего storage.json (кэш file_id не переносим — он без аккаунта)."""
        if self.get_meta("json_imported") or not legacy.exists():
            return
        try:
            data = json.loads(legacy.read_text("utf-8"))
            targets = {int(u): int(k) for u, k in (data.get("upload_targets") or {}).items()}
        except (OSError, ValueError, TypeError, AttributeError):
            log.exception("Не удалось прочитать %s — старые настройки не перенесены", legacy)
            targets = {}
        for user_id, kind in targets.items():
            if self.get_upload_target(user_id) is None:
                self.set_upload_target(user_id, kind)
        self.set_meta("json_imported", "1")
        log.info("Настройки из %s перенесены в базу", legacy.name)
