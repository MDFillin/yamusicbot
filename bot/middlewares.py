"""Пускаем к боту только владельцев (ALLOWED_USERS): бот управляет вашим аккаунтом Яндекс Музыки."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

log = logging.getLogger(__name__)


class AccessMiddleware(BaseMiddleware):
    def __init__(self, allowed_users: frozenset[int]) -> None:
        self._allowed = allowed_users

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = getattr(event, "from_user", None)
        if user is not None and user.id in self._allowed:
            return await handler(event, data)

        user_id = user.id if user else "?"
        log.warning("Отказано в доступе пользователю %s (@%s)", user_id, getattr(user, "username", None))
        text = (
            "⛔ Этот бот приватный.\n"
            f"Ваш Telegram ID: <code>{user_id}</code>\n"
            "Если это ваш бот — добавьте ID в ALLOWED_USERS и перезапустите его."
        )
        if isinstance(event, Message):
            await event.answer(text)
        elif isinstance(event, CallbackQuery):
            await event.answer("⛔ Нет доступа", show_alert=True)
        return None
