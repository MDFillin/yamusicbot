"""Проверка пользователя мини-приложения и подписанные ссылки на аудио."""

from __future__ import annotations

import hashlib
import hmac
import time
from datetime import UTC, datetime

from aiogram.utils.web_app import WebAppUser, safe_parse_webapp_init_data

INIT_DATA_MAX_AGE = 24 * 3600  # initData выдаётся при открытии приложения; сутки — с запасом
MEDIA_LINK_TTL = 6 * 3600


class AuthError(Exception):
    def __init__(self, status: int, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


def user_from_init_data(bot_token: str, init_data: str) -> WebAppUser:
    """initData подписан Telegram ключом из токена бота — подделать его без токена нельзя."""
    if not init_data:
        raise AuthError(401, "Откройте приложение через бота в Telegram")
    try:
        data = safe_parse_webapp_init_data(bot_token, init_data)
    except ValueError as e:
        raise AuthError(401, "Неверная подпись Telegram") from e
    auth_date = data.auth_date if data.auth_date.tzinfo else data.auth_date.replace(tzinfo=UTC)
    if (datetime.now(UTC) - auth_date).total_seconds() > INIT_DATA_MAX_AGE:
        raise AuthError(401, "Сессия устарела — закройте и откройте приложение заново")
    if data.user is None:
        raise AuthError(401, "Откройте приложение через бота в Telegram")
    return data.user


class MediaSigner:
    """Подписанные ссылки: <audio> и Telegram.downloadFile не умеют отправлять заголовки авторизации.

    В подпись входит Telegram ID: по ссылке трек берётся из аккаунта Яндекса именно этого человека.
    """

    def __init__(self, bot_token: str) -> None:
        self._key = hashlib.sha256(b"yamusicbot-media:" + bot_token.encode()).digest()

    def _sig(self, kind: str, user_id: int, track_id: str, exp: int) -> str:
        msg = f"{kind}:{user_id}:{track_id}:{exp}".encode()
        return hmac.new(self._key, msg, hashlib.sha256).hexdigest()[:32]

    def sign(self, kind: str, user_id: int, track_id: str, ttl: int = MEDIA_LINK_TTL) -> str:
        exp = int(time.time()) + ttl
        return f"u={user_id}&exp={exp}&sig={self._sig(kind, user_id, track_id, exp)}"

    def verify(self, kind: str, user_id: int, track_id: str, exp: str | None, sig: str | None) -> bool:
        if not exp or not sig or not exp.isdigit() or int(exp) < time.time():
            return False
        return hmac.compare_digest(self._sig(kind, user_id, track_id, int(exp)), sig)
