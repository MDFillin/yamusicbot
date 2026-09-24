"""Каждый пользователь работает со своим аккаунтом Яндекс Музыки: подставляем его клиент в обработчики."""

from __future__ import annotations

import html
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.dispatcher.flags import get_flag
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, TelegramObject

from bot.accounts import Accounts
from bot.callbacks import MenuCb
from bot.ym import YandexNotReady

log = logging.getLogger(__name__)

LOGIN_NEEDED = (
    "🔑 <b>Сначала подключите Яндекс Музыку</b>\n\n"
    "Бот работает с вашим собственным аккаунтом Яндекса. Вход — через страницу Яндекса, "
    "пароль бот не видит. Это займёт минуту."
)
YANDEX_PROBLEM = "⚠️ <b>Не получается подключиться к вашей Яндекс Музыке</b>\n\n{reason}"


def login_button(text: str = "🔑 Подключить Яндекс Музыку") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=text, callback_data=MenuCb(action="login").pack()),
    ]])


class AccountMiddleware(BaseMiddleware):
    """Кладёт в data["ym"] клиент Яндекса пользователя.

    Внутренняя middleware: к моменту вызова уже известно, какой обработчик сработал. Обработчикам с флагом
    public (старт, справка, вход) аккаунт не обязателен — им приходит ym=None и ym_error с причиной;
    остальным без подключённого аккаунта бот предлагает войти.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        accounts: Accounts = data["accounts"]
        public = bool(get_flag(data, "public"))
        ym, error = None, None
        if user is not None:
            try:
                ym = await accounts.get(user.id)
            except YandexNotReady as e:
                error = str(e)

        if ym is None and not public:
            if error:
                text = YANDEX_PROBLEM.format(reason=html.escape(error))
                await _tell(event, text, login_button("🔑 Подключить заново"), alert=f"⚠️ {error}")
            else:
                await _tell(event, LOGIN_NEEDED, login_button(), alert="🔑 Сначала подключите Яндекс Музыку: /login")
            return None

        data["ym"] = ym
        data["ym_error"] = error
        return await handler(event, data)


async def _tell(event: TelegramObject, text: str, markup: InlineKeyboardMarkup, alert: str) -> None:
    if isinstance(event, Message):
        await event.answer(text, reply_markup=markup)
    elif isinstance(event, CallbackQuery):
        await event.answer(alert[:190], show_alert=True)
