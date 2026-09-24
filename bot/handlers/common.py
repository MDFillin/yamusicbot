from __future__ import annotations

import html

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message, User

from bot.callbacks import MenuCb, NoopCb
from bot.config import Config
from bot.handlers.browse import playlists_text, show_source, target_prompt
from bot.keyboards import app_button, main_menu, welcome_menu
from bot.middlewares import YANDEX_PROBLEM, login_button
from bot.storage import Storage
from bot.ym import YandexMusic

router = Router(name="common")
PUBLIC = {"public": True}  # обработчик работает и без подключённой Яндекс Музыки

CREDIT = "<i>all rights reserved by @kpenkuu4au ❤️</i>"

HELP = f"""<b>🎧 Что умеет бот</b>

<b>Медиатека</b>
Кнопка «Медиатека» слева от поля ввода (или /app) открывает приложение: плейлисты, «Мне нравится», \
поиск, плеер, скачивание на телефон и загрузка файлов до сотен МБ.

<b>⬇️ Скачать</b>
• Напишите название трека или исполнителя — найду и пришлю MP3.
• Пришлите ссылку на трек, альбом, плейлист или артиста с music.yandex.ru.
• /likes — «Мне нравится», /playlists — ваши плейлисты. В каждом списке есть «Скачать всё».

<b>⬆️ Загрузить своё</b>
• Пришлите аудиофайл (можно несколько сразу) — бот покажет их и спросит, в какой плейлист загрузить.
• Кнопка ✏️ открывает редактор: название, исполнитель, альбом, год и обложка (пришлите картинку).
• Подпись к файлу <code>Исполнитель - Название</code> сразу пропишет эти теги.
• Загруженные треки встают в <b>начало</b> плейлиста — листать вниз не нужно.
• /target — плейлист по умолчанию: загрузка в него одной кнопкой.
• FLAC, M4A, WAV и другие форматы бот сам переведёт в MP3, обложка из файла сохранится.

<b>⚙️ Управление</b>
• Под каждым треком: ❤️ в любимые, ➕ добавить в плейлист.
• /newplaylist <i>название</i> — создать плейлист, удалить можно из его карточки.
• /settings — качество скачивания и аккаунт; оформление и токен — ⚙️ в «Медиатеке».
• /login — подключить (или сменить) аккаунт Яндекса, /logout — отключить.
• /cancel — отменить текущее действие.

{CREDIT}"""

WELCOME = """👋 <b>Привет, {name}!</b>

Я — бот для <b>Яндекс Музыки</b>:
🎧 медиатека и плеер в удобном приложении
⬇️ скачивание треков, альбомов и плейлистов в MP3
⬆️ загрузка ваших аудиофайлов прямо в Яндекс Музыку

Чтобы начать, подключите свой аккаунт Яндекса — это займёт минуту. \
Вход идёт через страницу Яндекса, пароль бот не видит."""

HOME = """🎧 <b>{login}</b> · {plus}

• Напишите название трека или пришлите ссылку — найду и скачаю.
• Пришлите аудиофайл — загружу в вашу Яндекс Музыку.
• Всё остальное — в медиатеке 👇"""


def plus_label(ym: YandexMusic) -> str:
    return "Плюс ✅" if ym.has_plus else "без Плюса (полные треки могут не скачиваться)"


def home(ym: YandexMusic, config: Config) -> tuple[str, InlineKeyboardMarkup]:
    text = HOME.format(login=html.escape(ym.login or "аккаунт Яндекса"), plus=plus_label(ym))
    return text, main_menu(config.webapp_url)


def welcome(user: User | None, config: Config) -> tuple[str, InlineKeyboardMarkup]:
    name = html.escape(user.first_name) if user and user.first_name else "друг"
    return WELCOME.format(name=name), welcome_menu(config.webapp_url)


@router.message(CommandStart(), flags=PUBLIC)
async def start(message: Message, state: FSMContext, config: Config, ym: YandexMusic | None,
                ym_error: str | None) -> None:
    await state.clear()
    if ym is not None:
        text, markup = home(ym, config)
    elif ym_error:
        text, markup = YANDEX_PROBLEM.format(reason=html.escape(ym_error)), login_button("🔑 Подключить заново")
    else:
        text, markup = welcome(message.from_user, config)
    await message.answer(text, reply_markup=markup)


@router.message(Command("menu"), flags=PUBLIC)
async def menu(message: Message, state: FSMContext, config: Config, ym: YandexMusic | None,
               ym_error: str | None) -> None:
    await start(message, state, config, ym, ym_error)


@router.message(Command("app"), flags=PUBLIC)
async def open_app(message: Message, config: Config) -> None:
    button = app_button(config.webapp_url)
    if button is None:
        await message.answer(
            "Мини-приложение ещё не настроено: владельцу бота нужно указать в .env публичный HTTPS-адрес "
            "WEBAPP_URL и перезапустить бота (см. README, раздел «Мини-приложение»)."
        )
        return
    await message.answer("🎧 Ваша медиатека: плейлисты, поиск, плеер, скачивание и загрузка треков.",
                         reply_markup=InlineKeyboardMarkup(inline_keyboard=[[button]]))


@router.message(Command("help"), flags=PUBLIC)
async def help_(message: Message) -> None:
    await message.answer(HELP)


@router.callback_query(MenuCb.filter(F.action == "help"), flags=PUBLIC)
async def help_button(call: CallbackQuery) -> None:
    await call.answer()
    await call.message.answer(HELP)


@router.message(Command("cancel"), flags=PUBLIC)
async def cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Ок, отменил.")


# ---------- кнопки главного меню ----------

@router.callback_query(MenuCb.filter(F.action == "likes"))
async def menu_likes(call: CallbackQuery, ym: YandexMusic) -> None:
    await call.answer()
    await show_source(call.message, ym, "likes")


@router.callback_query(MenuCb.filter(F.action == "playlists"))
async def menu_playlists(call: CallbackQuery, ym: YandexMusic) -> None:
    await call.answer()
    text, markup = await playlists_text(ym)
    await call.message.answer(text, reply_markup=markup)


@router.callback_query(MenuCb.filter(F.action == "target"))
async def menu_target(call: CallbackQuery, ym: YandexMusic, store: Storage) -> None:
    await call.answer()
    text, markup = await target_prompt(ym, store, call.from_user.id)
    await call.message.answer(text, reply_markup=markup)


# Подключается последним: ловит всё, что не обработали остальные роутеры.
fallback_router = Router(name="fallback")


@fallback_router.callback_query(NoopCb.filter(), flags=PUBLIC)
async def noop(call: CallbackQuery) -> None:
    await call.answer()


@fallback_router.callback_query(F.data, flags=PUBLIC)
async def stale(call: CallbackQuery) -> None:
    await call.answer("Кнопка устарела")


@fallback_router.message(flags=PUBLIC)
async def unknown(message: Message) -> None:
    await message.answer("🤔 Не понял. Напишите название трека, пришлите ссылку или аудиофайл. /help — справка.")
