"""База: входы пользователей зашифрованы, файлы только для владельца, быстрые записи (WAL)."""

import logging
import sqlite3
import stat

import pytest
from cryptography.fernet import Fernet

from bot.crypto import KEY_FILE, TokenCipher
from bot.storage import Storage

TOKEN = "y0_AgAAAAB-very-secret-yandex-token"


def raw_bytes(tmp_path) -> bytes:
    return b"".join(f.read_bytes() for f in tmp_path.glob("bot.db*"))


def test_tokens_are_encrypted_on_disk(tmp_path):
    store = Storage(tmp_path / "bot.db")
    store.set_account(1, TOKEN, "me")
    assert store.get_account(1) == (TOKEN, "me")
    [(stored,)] = sqlite3.connect(tmp_path / "bot.db").execute("SELECT token FROM accounts").fetchall()
    assert stored.startswith("enc1:") and TOKEN not in stored
    assert TOKEN.encode() not in raw_bytes(tmp_path), "ни в базе, ни в журнале WAL токена открытым текстом нет"
    store.close()


def test_old_plaintext_tokens_are_migrated(tmp_path):
    db = sqlite3.connect(tmp_path / "bot.db")
    db.execute("CREATE TABLE accounts (user_id INTEGER PRIMARY KEY, token TEXT NOT NULL, login TEXT, "
               "created_at INTEGER NOT NULL)")
    db.execute("INSERT INTO accounts VALUES (1, ?, 'me', 0)", (TOKEN,))
    db.commit()
    db.close()
    assert TOKEN.encode() in raw_bytes(tmp_path)

    store = Storage(tmp_path / "bot.db")
    assert store.get_account(1) == (TOKEN, "me")
    store.close()
    assert TOKEN.encode() not in raw_bytes(tmp_path), "старое открытое значение вычищено из файла"


def test_files_are_private_and_wal_is_on(tmp_path):
    store = Storage(tmp_path / "bot.db")
    store.set_account(1, TOKEN, "me")
    assert store._db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    for f in [*tmp_path.glob("bot.db*"), tmp_path / KEY_FILE]:
        assert stat.S_IMODE(f.stat().st_mode) == 0o600, f.name
    key = (tmp_path / KEY_FILE).read_bytes()
    store.close()
    Storage(tmp_path / "bot.db").close()
    assert (tmp_path / KEY_FILE).read_bytes() == key, "ключ создаётся один раз"


def test_lost_key_means_reconnect_not_crash(tmp_path, caplog):
    store = Storage(tmp_path / "bot.db")
    store.set_account(1, TOKEN, "me")
    store.close()
    (tmp_path / KEY_FILE).unlink()

    store = Storage(tmp_path / "bot.db")
    with caplog.at_level(logging.ERROR):
        assert store.get_account(1) is None
        assert store.get_account(1) is None
    assert len([r for r in caplog.records if "другим ключом" in r.getMessage()]) == 1
    store.set_account(1, "new-token", "me")  # человек подключил Яндекс заново
    assert store.get_account(1) == ("new-token", "me")
    store.close()


def test_switching_to_env_key_reencrypts(tmp_path):
    store = Storage(tmp_path / "bot.db")  # сначала ключ из data/secret.key
    store.set_account(1, TOKEN, "me")
    store.close()

    env_key = Fernet.generate_key().decode()
    store = Storage(tmp_path / "bot.db", TokenCipher.load(tmp_path, env_key))
    assert store.get_account(1) == (TOKEN, "me")
    store.close()

    (tmp_path / KEY_FILE).unlink()  # после переезда файл с ключом больше не нужен
    store = Storage(tmp_path / "bot.db", TokenCipher.load(tmp_path, env_key))
    assert store.get_account(1) == (TOKEN, "me")
    store.close()


def test_bad_env_key_is_explained(tmp_path):
    with pytest.raises(ValueError, match="ENCRYPTION_KEY"):
        TokenCipher.load(tmp_path, "not-a-key")


def test_backup_keeps_tokens_encrypted(tmp_path):
    store = Storage(tmp_path / "bot.db")
    store.set_account(1, TOKEN, "me")
    store.backup(tmp_path / "copy.sqlite")
    assert TOKEN.encode() not in (tmp_path / "copy.sqlite").read_bytes()
    store.close()
