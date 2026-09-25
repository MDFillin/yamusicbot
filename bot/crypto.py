"""Шифрование входов пользователей (OAuth-токенов Яндекс Музыки) в базе.

Что это даёт: база сама по себе — бэкап из админ-панели, скопированный или утёкший bot.db — токенов не
раскрывает. Если ключ задан в .env (ENCRYPTION_KEY), то не раскрывает их и вся папка data. Кто получил весь
сервер целиком (и .env, и data), получит и токены: боту они нужны в расшифрованном виде, чтобы работать.

Ключ: ENCRYPTION_KEY из .env, иначе data/secret.key (создаётся сам, права 600). Потеря ключа не ломает бота:
расшифровать старые входы не выйдет, и пользователи просто подключат Яндекс заново.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

log = logging.getLogger(__name__)

PREFIX = "enc1:"  # так помечены зашифрованные значения; без префикса — старые, ещё не зашифрованные
KEY_FILE = "secret.key"


class KeyMismatch(ValueError):
    """Значение зашифровано другим ключом."""


def _fernet(key: str | bytes, source: str) -> Fernet:
    try:
        return Fernet(key.strip() if isinstance(key, bytes) else key.strip().encode())
    except (ValueError, TypeError) as e:
        raise ValueError(f"{source}: ключ шифрования должен быть 44 символа из Fernet.generate_key() "
                         "(сгенерировать: python -c \"from cryptography.fernet import Fernet; "
                         "print(Fernet.generate_key().decode())\")") from e


class TokenCipher:
    def __init__(self, primary: Fernet, previous: list[Fernet] | None = None) -> None:
        self._previous = previous or []
        self._fernet = MultiFernet([primary, *self._previous])  # шифрует первым, расшифровывает любым

    @property
    def rotating(self) -> bool:
        """Есть прежний ключ: всё, что зашифровано им, надо перешифровать основным."""
        return bool(self._previous)

    @classmethod
    def load(cls, data_dir: Path, env_key: str | None = None) -> TokenCipher:
        key_file = data_dir / KEY_FILE
        file_key = key_file.read_bytes() if key_file.exists() else None
        if env_key:
            primary = _fernet(env_key, "ENCRYPTION_KEY")
            # Раньше работали с ключом из файла — он нужен, чтобы перешифровать старые записи новым ключом.
            previous = [_fernet(file_key, str(key_file))] if file_key and file_key.strip() != env_key.encode() else []
            return cls(primary, previous)
        if file_key is None:
            data_dir.mkdir(parents=True, exist_ok=True)
            file_key = Fernet.generate_key()
            fd = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(file_key)
            log.info("Создан ключ шифрования входов: %s — храните его вместе с бэкапами базы", key_file)
        return cls(_fernet(file_key, str(key_file)))

    @staticmethod
    def is_encrypted(stored: str) -> bool:
        return stored.startswith(PREFIX)

    def encrypt(self, value: str) -> str:
        return PREFIX + self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, stored: str) -> str:
        if not self.is_encrypted(stored):
            return stored  # запись из версии без шифрования; при запуске её перешифруют
        try:
            return self._fernet.decrypt(stored[len(PREFIX):].encode()).decode()
        except InvalidToken as e:
            raise KeyMismatch("значение зашифровано другим ключом") from e
