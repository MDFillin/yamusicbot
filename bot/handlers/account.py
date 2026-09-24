"""Подключение и отключение своего аккаунта Яндекс Музыки."""

from __future__ import annotations

import asyncio
import html
import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.accounts import Accounts, LoginError, LoginSession
from bot.callbacks import MenuCb, SettingsCb
from bot.config import Config
from bot.handlers.common import PUBLIC, home, plus_label
from bot.keyboards import app_button
from bot.middlewares import login_button
from bot.settings import QUALITY_LABELS, available_qualities, get_quality, set_quality
from bot.storage import Storage
from bot.ym import YandexMusic

log = logging.getLogger(__name__)
router = Router(name="account")

# Фоновые ожидания подтверждения входа (ссылки держим, чтобы задачи не собрал сборщик мусора).
_waiters: set[asyncio.Task] = set()

LOGIN_TEXT = """🔑 <b>Вход в Яндекс Музыку</b>

1. Откройте страницу Яндекса — кнопка ниже ({url})
2. Введите код: <code>{code}</code>
3. Подтвердите вход своим аккаунтом

Я сам пойму, что всё готово. Код действует {minutes} мин.{note}"""


def _login_markup(session: LoginSession) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌐 Открыть страницу входа", url=session.url)],
        [InlineKeyboardButton(text="✖️ Отмена", callback_data=MenuCb(action="login_cancel").pack())],
    ])


async def _safe_edit(message: Message, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
    try:
        await message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest:
        pass  # сообщение удалили или уже изменили


async def _finish_login(message: Message, session: LoginSession, config: Config) -> None:
    try:
        ym = await session.wait()
    except LoginError as e:
        await _safe_edit(message, f"😕 {html.escape(str(e))}", login_button("🔁 Попробовать ещё раз"))
        return
    except asyncio.CancelledError:
        if session.status == "cancelled":
            await _safe_edit(message, "Вход отменён.")
            return
        raise
    text, markup = home(ym, config)
    await _safe_edit(message, "✅ <b>Яндекс Музыка подключена!</b>\n\n" + text, markup)


async def begin_login(message: Message, user_id: int, accounts: Accounts, config: Config,
                      ym: YandexMusic | None) -> None:
    try:
        session = await accounts.start_login(user_id)
    except LoginError as e:
        await message.answer(f"😕 {html.escape(str(e))}")
        return
    note = ""
    if ym is not None and ym.login:
        note = f"\n\nСейчас подключён <b>{html.escape(ym.login)}</b> — после входа он заменится новым."
    text = LOGIN_TEXT.format(
        url=html.escape(session.url.removeprefix("https://")), code=html.escape(session.code),
        minutes=max(1, session.expires_in // 60), note=note,
    )
    sent = await message.answer(text, reply_markup=_login_markup(session), disable_web_page_preview=True)
    task = asyncio.create_task(_finish_login(sent, session, config))
    _waiters.add(task)
    task.add_done_callback(_waiters.discard)


@router.message(Command("login"), flags=PUBLIC)
async def cmd_login(message: Message, accounts: Accounts, config: Config, ym: YandexMusic | None) -> None:
    await begin_login(message, message.from_user.id, accounts, config, ym)


@router.callback_query(MenuCb.filter(F.action == "login"), flags=PUBLIC)
async def on_login(call: CallbackQuery, accounts: Accounts, config: Config, ym: YandexMusic | None) -> None:
    await call.answer()
    await begin_login(call.message, call.from_user.id, accounts, config, ym)


@router.callback_query(MenuCb.filter(F.action == "login_cancel"), flags=PUBLIC)
async def on_login_cancel(call: CallbackQuery, accounts: Accounts) -> None:
    accounts.cancel_login(call.from_user.id)
    await call.answer("Вход отменён")
    await _safe_edit(call.message, "Вход отменён.")


def settings_view(ym: YandexMusic, store: Storage, config: Config, user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    quality = get_quality(store, config, user_id, "download")
    text = (
        "⚙️ <b>Настройки</b>\n\n"
        f"👤 Аккаунт Яндекса: <b>{html.escape(ym.login or '—')}</b>\n"
        f"Подписка: {plus_label(ym)}\n\n"
        f"⬇️ Качество скачивания и отправки в чат: <b>{quality} kbps</b> ({QUALITY_LABELS.get(quality, '')})\n"
        "Меньше — быстрее и экономнее, 320 — лучшее (нужен Плюс).\n\n"
        "Оформление, качество в плеере и токен Яндекса — в настройках приложения (⚙️ в «Медиатеке»)."
    )
    rows = [[
        InlineKeyboardButton(text=f"✅ {q}" if q == quality else str(q),
                             callback_data=SettingsCb(kind="download", value=q).pack())
        for q in available_qualities(config)
    ]]
    if button := app_button(config.webapp_url, "🎨 Настройки приложения"):
        rows.append([button])
    rows.append([
        InlineKeyboardButton(text="🔁 Сменить аккаунт", callback_data=MenuCb(action="login").pack()),
        InlineKeyboardButton(text="🚪 Отключить", callback_data=MenuCb(action="logout").pack()),
    ])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(Command("settings"))
async def cmd_settings(message: Message, ym: YandexMusic, store: Storage, config: Config) -> None:
    text, markup = settings_view(ym, store, config, message.from_user.id)
    await message.answer(text, reply_markup=markup)


@router.callback_query(MenuCb.filter(F.action == "account"))
async def on_account(call: CallbackQuery, ym: YandexMusic, store: Storage, config: Config) -> None:
    await call.answer()
    text, markup = settings_view(ym, store, config, call.from_user.id)
    await call.message.answer(text, reply_markup=markup)


@router.callback_query(SettingsCb.filter())
async def on_setting(call: CallbackQuery, callback_data: SettingsCb, ym: YandexMusic, store: Storage,
                     config: Config) -> None:
    try:
        value = set_quality(store, config, call.from_user.id, callback_data.kind, callback_data.value)
    except ValueError as e:
        await call.answer(str(e), show_alert=True)
        return
    await call.answer(f"Качество: {value} kbps")
    text, markup = settings_view(ym, store, config, call.from_user.id)
    await _safe_edit(call.message, text, markup)


LOGOUT_CONFIRM = (
    "🚪 Отключить аккаунт <b>{login}</b>?\n\n"
    "Бот удалит ваш вход со своего сервера и перестанет работать с вашей музыкой. "
    "Сама музыка и плейлисты в Яндексе останутся. Полностью отозвать доступ можно в Яндекс ID "
    "(id.yandex.ru, раздел с устройствами и входами)."
)


async def _ask_logout(message: Message, ym: YandexMusic | None) -> None:
    if ym is None:
        await message.answer("Яндекс Музыка и так не подключена. Подключить: /login")
        return
    markup = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🚪 Да, отключить", callback_data=MenuCb(action="logout_ok").pack()),
        InlineKeyboardButton(text="Отмена", callback_data=MenuCb(action="logout_no").pack()),
    ]])
    await message.answer(LOGOUT_CONFIRM.format(login=html.escape(ym.login or "Яндекса")), reply_markup=markup)


@router.message(Command("logout"), flags=PUBLIC)
async def cmd_logout(message: Message, ym: YandexMusic | None) -> None:
    await _ask_logout(message, ym)


@router.callback_query(MenuCb.filter(F.action == "logout"), flags=PUBLIC)
async def on_logout(call: CallbackQuery, ym: YandexMusic | None) -> None:
    await call.answer()
    await _ask_logout(call.message, ym)


@router.callback_query(MenuCb.filter(F.action == "logout_ok"), flags=PUBLIC)
async def on_logout_ok(call: CallbackQuery, accounts: Accounts) -> None:
    await accounts.logout(call.from_user.id)
    await call.answer("Аккаунт отключён")
    await _safe_edit(call.message, "✅ Аккаунт отключён, бот забыл ваш вход.\nПодключить снова — /login")


@router.callback_query(MenuCb.filter(F.action == "logout_no"), flags=PUBLIC)
async def on_logout_no(call: CallbackQuery) -> None:
    await call.answer()
    await _safe_edit(call.message, "Ок, аккаунт остаётся подключённым.")
