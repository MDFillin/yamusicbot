"""Пускаем к боту только владельцев (ALLOWED_USERS): бот управляет вашим аккаунтом Яндекс Музыки."""

from __future__ import annotations

import html
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from bot.ym import YandexMusic, YandexNotReady

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


YANDEX_HELP = (
    "⚠️ Бот запущен, но не может подключиться к Яндекс Музыке.\n\n"
    "{reason}\n\n"
    "<b>Что сделать на сервере</b> (в папке бота):\n"
    "1. Получить новый токен: <code>docker compose run --rm bot python -m bot.get_token</code>\n"
    "2. Вписать его в .env: <code>nano .env</code> → строка YANDEX_MUSIC_TOKEN=…\n"
    "3. Применить: <code>docker compose up -d --force-recreate</code>\n\n"
    "Если токен точно свежий — Яндекс может не пускать запросы с IP этого сервера; помогает сервер в России."
)


class YandexReadyMiddleware(BaseMiddleware):
    """Если Яндекс Музыка не подключилась, объясняет это в чате вместо молчания."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        ym: YandexMusic = data["ym"]
        try:
            await ym.ensure_started()
        except YandexNotReady as e:
            if isinstance(event, Message):
                await event.answer(YANDEX_HELP.format(reason=html.escape(str(e))))
            elif isinstance(event, CallbackQuery):
                await event.answer(f"⚠️ Нет связи с Яндекс Музыкой: {e}"[:190], show_alert=True)
            return None
        return await handler(event, data)
