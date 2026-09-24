"""Хранилище бота в SQLite: аккаунты Яндекса пользователей, их настройки и кэш file_id отправленных треков."""

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
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class Storage:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, isolation_level=None)  # autocommit: каждая запись сразу на диске
        self._db.executescript(SCHEMA)
        try:
            os.chmod(path, 0o600)  # тут токены пользователей
        except OSError:
            pass
        self._import_json(path.with_name("storage.json"))

    def close(self) -> None:
        self._db.close()

    def _one(self, sql: str, *args) -> tuple | None:
        return self._db.execute(sql, args).fetchone()

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
