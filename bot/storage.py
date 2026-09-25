"""Хранилище бота в SQLite: аккаунты Яндекса пользователей, их настройки, кэш file_id и данные для админ-панели."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from pathlib import Path

from bot.crypto import KeyMismatch, TokenCipher

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    user_id INTEGER PRIMARY KEY,   -- Telegram ID
    token TEXT NOT NULL,           -- OAuth-токен Яндекс Музыки, зашифрованный (bot/crypto.py)
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
-- Админы, назначенные владельцем из приложения (владельцы — ADMIN_IDS в .env, их здесь нет).
CREATE TABLE IF NOT EXISTS admins (
    user_id INTEGER PRIMARY KEY,
    granted_by INTEGER NOT NULL,
    granted_at INTEGER NOT NULL
);
-- Статистика прослушиваний: история Яндекса, собранная ботом (день, трек, откуда играл).
CREATE TABLE IF NOT EXISTS listens (
    user_id INTEGER NOT NULL,
    day TEXT NOT NULL,             -- ГГГГ-ММ-ДД, как в истории Яндекса
    track_id TEXT NOT NULL,
    context TEXT NOT NULL,         -- album:ID | playlist:UID:KIND | artist:ID | wave:SEEDS | none
    PRIMARY KEY (user_id, day, track_id, context)
);
CREATE TABLE IF NOT EXISTS track_meta (
    track_id TEXT PRIMARY KEY,
    title TEXT,
    album_id TEXT,
    album TEXT,
    genre TEXT,
    duration_ms INTEGER,
    cover_uri TEXT
);
CREATE TABLE IF NOT EXISTS track_artists (
    track_id TEXT NOT NULL,
    pos INTEGER NOT NULL,
    artist_id TEXT NOT NULL,
    name TEXT,
    PRIMARY KEY (track_id, pos)
);
CREATE TABLE IF NOT EXISTS contexts (
    key TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    title TEXT
);
CREATE INDEX IF NOT EXISTS listens_user_track ON listens (user_id, track_id);
CREATE INDEX IF NOT EXISTS track_artists_artist ON track_artists (artist_id);
CREATE INDEX IF NOT EXISTS users_last_seen ON users (last_seen);
CREATE INDEX IF NOT EXISTS activity_day ON activity (day);
CREATE INDEX IF NOT EXISTS counters_day ON counters (day, key);
"""


class Storage:
    def __init__(self, path: Path, cipher: TokenCipher | None = None) -> None:
        self.path = path
        self._cipher = cipher or TokenCipher.load(path.parent)
        self._undecryptable: set[int] = set()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, isolation_level=None)  # autocommit: каждая запись сразу в базе
        # Бот — один процесс с одним потоком событий: соединение одно, пул не нужен (запросы и так идут по очереди).
        # Дорогим было другое — fsync на каждую запись (~1,5 мс, и весь бот в это время стоит). В режиме WAL
        # с synchronous=NORMAL запись ~0,06 мс; при внезапном отключении питания могут потеряться последние
        # доли секунды записей, но база остаётся целой.
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.execute("PRAGMA busy_timeout=5000")  # если базу держит кто-то ещё (sqlite3 в консоли) — подождать
        self._db.execute("PRAGMA secure_delete=ON")  # удалённое (входы после /logout) затирается нулями
        # LOWER в SQLite понимает только латиницу, а искать людей нужно и по-русски.
        self._db.create_function("PYLOWER", 1, lambda v: v.lower() if isinstance(v, str) else v, deterministic=True)
        self._db.executescript(SCHEMA)
        for file in (path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")):
            try:
                os.chmod(file, 0o600)  # тут входы пользователей (журнал WAL тоже их содержит)
            except OSError:
                pass
        self._import_json(path.with_name("storage.json"))
        self._backfill_users()
        self._encrypt_tokens()

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
        row = self._one("SELECT token, login FROM accounts WHERE user_id = ?", user_id)
        if row is None:
            return None
        try:
            return self._cipher.decrypt(row[0]), row[1]
        except KeyMismatch:
            if user_id not in self._undecryptable:
                self._undecryptable.add(user_id)
                log.error("Вход пользователя %s зашифрован другим ключом (сменился ENCRYPTION_KEY или удалён "
                          "data/secret.key?) — ему придётся подключить Яндекс заново", user_id)
            return None  # для бота это «Яндекс не подключён»: человек войдёт снова, и запись перезапишется

    def set_account(self, user_id: int, token: str, login: str | None) -> None:
        self._undecryptable.discard(user_id)
        self._db.execute(
            "INSERT INTO accounts (user_id, token, login, created_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (user_id) DO UPDATE SET token = excluded.token, login = excluded.login",
            (user_id, self._cipher.encrypt(token), login, int(time.time())),
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

    def _encrypt_tokens(self) -> None:
        """Шифрует входы, сохранённые прежними версиями, и перешифровывает основным ключом после его смены."""
        changed = 0
        for user_id, stored in self._all("SELECT user_id, token FROM accounts"):
            if self._cipher.is_encrypted(stored) and not self._cipher.rotating:
                continue
            try:
                token = self._cipher.decrypt(stored)
            except KeyMismatch:
                continue  # ни один ключ не подходит — get_account объяснит в логе
            self._db.execute("UPDATE accounts SET token = ? WHERE user_id = ?", (self._cipher.encrypt(token), user_id))
            changed += 1
        if changed:
            # Старые открытые значения могли остаться в свободных страницах файла и в журнале — убираем.
            self._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self._db.execute("VACUUM")
            log.info("Входы пользователей в базе зашифрованы: %s", changed)

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

    # ---------- назначенные админы ----------

    def admin_ids(self) -> set[int]:
        return {r[0] for r in self._all("SELECT user_id FROM admins")}

    def get_admin(self, user_id: int) -> tuple[int, int] | None:
        """(кто назначил, когда) или None."""
        return self._one("SELECT granted_by, granted_at FROM admins WHERE user_id = ?", user_id)

    def add_admin(self, user_id: int, granted_by: int) -> None:
        self._db.execute("INSERT OR IGNORE INTO admins (user_id, granted_by, granted_at) VALUES (?, ?, ?)",
                         (user_id, granted_by, int(time.time())))

    def remove_admin(self, user_id: int) -> bool:
        return bool(self._db.execute("DELETE FROM admins WHERE user_id = ?", (user_id,)).rowcount)

    def users_by_ids(self, ids: list[int]) -> list[dict]:
        if not ids:
            return []
        marks = ",".join("?" * len(ids))
        return self._users(f"WHERE u.user_id IN ({marks}) ORDER BY u.last_seen DESC", tuple(ids), len(ids))

    # ---------- статистика прослушиваний ----------

    def add_listens(self, user_id: int, rows: list[tuple[str, str, str]]) -> int:
        """rows: (день, трек, контекст). Возвращает, сколько записей новых."""
        before = self._db.total_changes
        self._db.execute("BEGIN")
        try:
            self._db.executemany("INSERT OR IGNORE INTO listens (user_id, day, track_id, context) VALUES (?, ?, ?, ?)",
                                 [(user_id, *r) for r in rows])
            self._db.execute("COMMIT")
        except BaseException:
            self._db.execute("ROLLBACK")
            raise
        return self._db.total_changes - before

    def known_tracks(self, track_ids: list[str]) -> set[str]:
        found: set[str] = set()
        for i in range(0, len(track_ids), 500):
            chunk = track_ids[i:i + 500]
            marks = ",".join("?" * len(chunk))
            found |= {r[0] for r in self._all(f"SELECT track_id FROM track_meta WHERE track_id IN ({marks})", *chunk)}
        return found

    def save_track_meta(self, track_id: str, title: str | None, album_id: str | None, album: str | None,
                        genre: str | None, duration_ms: int | None, cover_uri: str | None,
                        artists: list[tuple[str, str | None]]) -> None:
        self._db.execute("INSERT OR REPLACE INTO track_meta VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (track_id, title, album_id, album, genre, duration_ms, cover_uri))
        self._db.execute("DELETE FROM track_artists WHERE track_id = ?", (track_id,))
        self._db.executemany("INSERT INTO track_artists VALUES (?, ?, ?, ?)",
                             [(track_id, i, artist_id, name) for i, (artist_id, name) in enumerate(artists)])

    def unknown_contexts(self, keys: list[str]) -> list[str]:
        known: set[str] = set()
        for i in range(0, len(keys), 500):
            chunk = keys[i:i + 500]
            marks = ",".join("?" * len(chunk))
            known |= {r[0] for r in self._all(f"SELECT key FROM contexts WHERE key IN ({marks})", *chunk)}
        return [k for k in keys if k not in known]

    def save_context(self, key: str, type_: str, title: str | None) -> None:
        self._db.execute("INSERT OR REPLACE INTO contexts VALUES (?, ?, ?)", (key, type_, title))

    def delete_listens(self, user_id: int) -> int:
        return self._db.execute("DELETE FROM listens WHERE user_id = ?", (user_id,)).rowcount

    def listening_since(self, user_id: int) -> str | None:
        return self._one("SELECT MIN(day) FROM listens WHERE user_id = ?", user_id)[0]

    def listening_days(self, user_id: int) -> list[str]:
        return [r[0] for r in self._all("SELECT DISTINCT day FROM listens WHERE user_id = ? ORDER BY day", user_id)]

    def listening_stats(self, user_id: int, start: str, end: str, top: int = 10) -> dict:
        """Статистика за дни start..end включительно. Прослушивание = пара (день, трек): повтор трека в тот же
        день история Яндекса не различает, а один трек из двух источников за день считаем один раз."""
        args = (user_id, start, end)
        plays = "SELECT DISTINCT day, track_id FROM listens WHERE user_id = ? AND day BETWEEN ? AND ?"
        total, minutes = self._one(
            f"SELECT COUNT(*), COALESCE(SUM(m.duration_ms), 0) / 60000 FROM ({plays}) p "
            "LEFT JOIN track_meta m USING (track_id)", *args)
        tracks, artists = self._one(
            f"SELECT COUNT(DISTINCT p.track_id), COUNT(DISTINCT a.artist_id) FROM ({plays}) p "
            "LEFT JOIN track_artists a USING (track_id)", *args)
        by_day = dict(self._all(f"SELECT day, COUNT(*) FROM ({plays}) GROUP BY day", *args))
        top_artists = self._all(
            f"SELECT a.artist_id, MAX(a.name), COUNT(*) c FROM ({plays}) p JOIN track_artists a USING (track_id) "
            "GROUP BY a.artist_id ORDER BY c DESC, MAX(a.name) LIMIT ?", *args, top)
        top_tracks = self._all(
            f"SELECT p.track_id, COUNT(*) c FROM ({plays}) p GROUP BY p.track_id ORDER BY c DESC, "
            "MAX(p.day) DESC LIMIT ?", *args, top)
        top_albums = self._all(
            f"SELECT m.album_id, MAX(m.album), COUNT(*) c FROM ({plays}) p JOIN track_meta m USING (track_id) "
            "WHERE m.album_id IS NOT NULL GROUP BY m.album_id ORDER BY c DESC LIMIT ?", *args, top)
        genres = self._all(
            f"SELECT m.genre, COUNT(*) c FROM ({plays}) p JOIN track_meta m USING (track_id) "
            "WHERE m.genre IS NOT NULL AND m.genre != '' GROUP BY m.genre ORDER BY c DESC", *args)
        sources = self._all(
            "SELECT COALESCE(c.type, CASE WHEN l.context = 'none' THEN 'other' ELSE substr(l.context, 1, "
            "instr(l.context, ':') - 1) END) t, COUNT(*) n FROM listens l LEFT JOIN contexts c ON c.key = l.context "
            "WHERE l.user_id = ? AND l.day BETWEEN ? AND ? GROUP BY t ORDER BY n DESC", *args)
        top_sources = self._all(
            "SELECT l.context, MAX(c.type), MAX(c.title), COUNT(*) n FROM listens l LEFT JOIN contexts c "
            "ON c.key = l.context WHERE l.user_id = ? AND l.day BETWEEN ? AND ? AND l.context != 'none' "
            "GROUP BY l.context ORDER BY n DESC LIMIT ?", *args, 5)
        new_tracks = self._one(
            "SELECT COUNT(*) FROM (SELECT track_id FROM listens WHERE user_id = ? GROUP BY track_id "
            "HAVING MIN(day) BETWEEN ? AND ?)", *args)[0]
        new_artists = self._all(
            "SELECT a.artist_id, MAX(a.name) FROM listens l JOIN track_artists a USING (track_id) "
            "WHERE l.user_id = ? GROUP BY a.artist_id HAVING MIN(l.day) BETWEEN ? AND ? "
            "ORDER BY COUNT(*) DESC", *args)
        return {
            "plays": total, "minutes": minutes, "tracks": tracks, "artists": artists, "by_day": by_day,
            "top_artists": [{"id": i, "name": n, "plays": c} for i, n, c in top_artists],
            "top_tracks": [{"id": i, "plays": c} for i, c in top_tracks],
            "top_albums": [{"id": i, "title": t, "plays": c} for i, t, c in top_albums],
            "genres": [{"id": g, "plays": c} for g, c in genres],
            "sources": [{"type": t, "plays": n} for t, n in sources],
            "top_sources": [{"key": k, "type": t, "title": title, "plays": n} for k, t, title, n in top_sources],
            "new_tracks": new_tracks,
            "new_artists": [{"id": i, "name": n} for i, n in new_artists],
        }

    def track_names(self, track_ids: list[str]) -> dict[str, tuple[str, str]]:
        """{трек: (название, «Исполнитель, Исполнитель»)} из кэша данных треков."""
        result: dict[str, tuple[str, str]] = {}
        for track_id in track_ids:
            row = self._one("SELECT title FROM track_meta WHERE track_id = ?", track_id)
            if row and row[0]:
                names = [r[0] for r in self._all(
                    "SELECT name FROM track_artists WHERE track_id = ? ORDER BY pos", track_id) if r[0]]
                result[track_id] = (row[0], ", ".join(names))
        return result

    def album_title(self, album_id: str) -> str | None:
        row = self._one("SELECT album FROM track_meta WHERE album_id = ? AND album IS NOT NULL LIMIT 1", album_id)
        return row[0] if row else None

    def artist_name(self, artist_id: str) -> str | None:
        row = self._one("SELECT name FROM track_artists WHERE artist_id = ? AND name IS NOT NULL LIMIT 1", artist_id)
        return row[0] if row else None

    def stats_users(self) -> list[int]:
        return [r[0] for r in self._all("SELECT user_id FROM user_settings WHERE key = 'stats' AND value = '1'")]

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
