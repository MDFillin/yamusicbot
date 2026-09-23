from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo

from bot.callbacks import NoopCb
from bot.config import Config
from bot.ym import YandexMusic

router = Router(name="common")

HELP = """<b>🎧 Бот для Яндекс Музыки</b>

<b>Медиатека</b> — кнопка «Медиатека» слева от поля ввода (или /app) открывает приложение:
плейлисты, «Мне нравится», поиск, плеер, скачивание на телефон и загрузка файлов до сотен МБ.

<b>Скачивание</b>
• Напишите название трека или исполнителя — найду и пришлю MP3.
• Пришлите ссылку на трек, альбом, плейлист или артиста с music.yandex.ru.
• /likes — «Мне нравится», /playlists — ваши плейлисты. В любом списке есть кнопка «Скачать всё».

<b>Загрузка своих треков</b>
• Пришлите аудиофайл (можно несколько сразу) — бот спросит, в какой плейлист его загрузить.
• Подпись к файлу вида <code>Исполнитель - Название</code> пропишет эти теги в трек.
• /target — выбрать плейлист, куда файлы будут загружаться сразу, без вопроса.
• Не-MP3 (FLAC, M4A, WAV…) бот сам сконвертирует в MP3, если установлен ffmpeg.

<b>Управление</b>
• Под каждым треком: ❤️ в любимые, ➕ добавить в плейлист.
• /newplaylist <i>название</i> — создать плейлист, удалить можно из его карточки.
• /cancel — отменить текущее действие."""


def app_button(config: Config) -> InlineKeyboardMarkup | None:
    if not config.webapp_url:
        return None
    button = InlineKeyboardButton(text="🎧 Открыть медиатеку", web_app=WebAppInfo(url=config.webapp_url))
    return InlineKeyboardMarkup(inline_keyboard=[[button]])


@router.message(CommandStart())
async def start(message: Message, ym: YandexMusic, config: Config) -> None:
    plus = "есть" if ym.has_plus else "нет (скачивание полных треков может не работать)"
    text = f"Аккаунт Яндекса: <b>{ym.login}</b>, Плюс: {plus}.\n\n{HELP}"
    await message.answer(text, reply_markup=app_button(config))


@router.message(Command("app"))
async def open_app(message: Message, config: Config) -> None:
    markup = app_button(config)
    if markup is None:
        await message.answer(
            "Мини-приложение ещё не настроено: укажите в .env публичный HTTPS-адрес WEBAPP_URL "
            "и перезапустите бота (см. README, раздел «Мини-приложение»)."
        )
        return
    await message.answer("Ваша медиатека: плейлисты, поиск, плеер, скачивание и загрузка треков.", reply_markup=markup)


@router.message(Command("help"))
async def help_(message: Message) -> None:
    await message.answer(HELP)


@router.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Ок, отменил.")


# Подключается последним: ловит всё, что не обработали остальные роутеры.
fallback_router = Router(name="fallback")


@fallback_router.callback_query(NoopCb.filter())
async def noop(call: CallbackQuery) -> None:
    await call.answer()


@fallback_router.callback_query(F.data)
async def stale(call: CallbackQuery) -> None:
    await call.answer("Кнопка устарела")


@fallback_router.message()
async def unknown(message: Message) -> None:
    await message.answer("Не понял. Напишите название трека, пришлите ссылку или аудиофайл. /help — справка.")
