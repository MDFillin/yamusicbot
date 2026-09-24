from __future__ import annotations

import html
import logging
import os
import sys
from typing import Any

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramNetworkError, TelegramUnauthorizedError
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, ErrorEvent, MenuButtonDefault, MenuButtonWebApp, User, WebAppInfo
from aiogram.utils.token import TokenValidationError
from aiohttp import web
from yandex_music.exceptions import UnauthorizedError

from bot.accounts import Accounts
from bot.audio import ffmpeg_available
from bot.config import Config, ConfigError, load_config
from bot.handlers import build_router
from bot.handlers.upload import UploadQueue
from bot.middlewares import AccountMiddleware
from bot.sender import TrackSender
from bot.storage import Storage
from bot.web.app import create_app

log = logging.getLogger("bot")

COMMANDS = [
    BotCommand(command="app", description="🎧 Открыть медиатеку"),
    BotCommand(command="likes", description="❤️ Мне нравится"),
    BotCommand(command="playlists", description="📃 Мои плейлисты"),
    BotCommand(command="target", description="📌 Куда загружать мои файлы"),
    BotCommand(command="newplaylist", description="➕ Создать плейлист"),
    BotCommand(command="menu", description="🏠 Главное меню"),
    BotCommand(command="login", description="🔑 Подключить Яндекс Музыку"),
    BotCommand(command="logout", description="🚪 Отключить аккаунт"),
    BotCommand(command="help", description="❓ Справка"),
    BotCommand(command="cancel", description="Отменить действие"),
]

# Что видит человек, впервые открыв бота (ставим, только если владелец не задал своё в @BotFather).
DESCRIPTION = (
    "🎧 Бот для Яндекс Музыки\n\n"
    "• Медиатека в удобном приложении: плейлисты, «Мне нравится», поиск и плеер\n"
    "• Скачивание треков, альбомов и плейлистов в MP3\n"
    "• Загрузка своих аудиофайлов прямо в Яндекс Музыку\n\n"
    "Нажмите «Старт» и подключите свой аккаунт Яндекса."
)
SHORT_DESCRIPTION = "Слушайте, скачивайте и загружайте музыку в свою Яндекс Музыку"
REVOKED = "⚠️ Яндекс Музыка не приняла ваш вход — возможно, он устарел или отозван. Подключите аккаунт заново: /login"


def build_bot(config: Config) -> Bot:
    session_kwargs: dict[str, Any] = {}
    if config.bot_api_url:
        session_kwargs["api"] = TelegramAPIServer.from_base(config.bot_api_url, is_local=config.bot_api_local)
    if config.telegram_proxy:
        session_kwargs["proxy"] = config.telegram_proxy
    session = AiohttpSession(**session_kwargs) if session_kwargs else None
    return Bot(config.bot_token, session=session, default=DefaultBotProperties(parse_mode=ParseMode.HTML))


def build_dependencies(config: Config, bot: Bot, accounts: Accounts, store: Storage) -> dict[str, Any]:
    """Объекты, которые aiogram подставляет в обработчики по имени аргумента (ym — AccountMiddleware)."""
    return {
        "config": config,
        "accounts": accounts,
        "store": store,
        "sender": TrackSender(bot, store, config),
        "uploads": UploadQueue(),
    }


def build_dispatcher(config: Config, bot: Bot, accounts: Accounts, store: Storage) -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage(), **build_dependencies(config, bot, accounts, store))

    # У каждого свой аккаунт Яндекса: middleware подставляет его клиент или предлагает войти.
    account = AccountMiddleware()
    for observer in (dp.message, dp.callback_query):
        observer.middleware(account)
    dp.include_router(build_router())

    @dp.errors()
    async def on_error(event: ErrorEvent) -> None:
        update = event.update
        source = update.message or update.callback_query
        user = source.from_user if source else None
        if isinstance(event.exception, UnauthorizedError):
            # Вход отозвали уже после подключения: забываем клиент, при следующем запросе бот всё объяснит.
            if user is not None:
                await dp["accounts"].reset(user.id)
            text = REVOKED
        else:
            log.exception("Ошибка при обработке апдейта", exc_info=event.exception)
            name = html.escape(type(event.exception).__name__)
            text = f"⚠️ Ошибка: {name}: {html.escape(str(event.exception))[:500]}"
        try:
            if update.message:
                await update.message.answer(text)
            elif update.callback_query:
                await update.callback_query.answer("⚠️ Ошибка, подробности в чате")
                if update.callback_query.message:
                    await update.callback_query.message.answer(text)
        except Exception:
            log.exception("Не удалось сообщить об ошибке пользователю")

    return dp


async def start_web(config: Config, bot: Bot, accounts: Accounts, store: Storage,
                    sender: TrackSender) -> web.AppRunner:
    runner = web.AppRunner(create_app(config, bot, accounts, store, sender), access_log=None)
    await runner.setup()
    await web.TCPSite(runner, config.web_host, config.web_port).start()
    log.info("Мини-приложение слушает http://%s:%s (публичный адрес: %s)",
             config.web_host, config.web_port, config.webapp_url or "WEBAPP_URL не задан")
    return runner


async def setup_menu_button(bot: Bot, config: Config) -> None:
    if config.webapp_url:
        button = MenuButtonWebApp(text="Медиатека", web_app=WebAppInfo(url=config.webapp_url))
        await bot.set_chat_menu_button(menu_button=button)
    else:
        await bot.set_chat_menu_button(menu_button=MenuButtonDefault())


async def setup_description(bot: Bot) -> None:
    try:
        if not (await bot.get_my_description()).description:
            await bot.set_my_description(DESCRIPTION)
        if not (await bot.get_my_short_description()).short_description:
            await bot.set_my_short_description(SHORT_DESCRIPTION)
    except TelegramAPIError as e:
        log.warning("Не удалось задать описание бота: %s", e)


async def check_telegram(bot: Bot) -> User:
    """Проверяет токен и связь с Telegram; при проблеме завершает программу с понятным объяснением."""
    try:
        me = await bot.get_me()
        # Если у бота когда-то был включён webhook, Telegram не отдаёт сообщения через polling — снимаем его.
        await bot.delete_webhook()
        return me
    except TelegramUnauthorizedError:
        await bot.session.close()
        sys.exit("Telegram отклонил BOT_TOKEN: скопируйте токен из @BotFather заново (в .env, строка BOT_TOKEN=)")
    except TelegramNetworkError as e:
        await bot.session.close()
        sys.exit(
            f"Не удалось связаться с Telegram (api.telegram.org): {e}\n"
            "Проверьте на сервере: curl -m 15 -sS -o /dev/null -w '%{http_code}\\n' https://api.telegram.org\n"
            "Ответ 302 — Telegram доступен, попробуйте DOCKER_MTU=1400 в .env. Таймаут — провайдер не пускает "
            "к Telegram: нужен TELEGRAM_PROXY или другой сервер. Подробно: docs/GUIDE.md, «Бот совсем не отвечает»."
        )


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        config = load_config()
    except ConfigError as e:
        sys.exit(f"Ошибка настройки: {e}")
    os.umask(0o077)  # база с токенами пользователей — только для владельца сервера
    if not ffmpeg_available():
        log.warning("ffmpeg не найден: не-MP3 файлы будут загружаться без конвертации")

    # Сначала Telegram: без него бот бесполезен, и о проблеме надо сказать сразу и понятно.
    try:
        bot = build_bot(config)
    except TokenValidationError:
        sys.exit("BOT_TOKEN выглядит неправильно: он должен быть вида 123456789:AAH... (скопируйте из @BotFather)")
    me = await check_telegram(bot)
    log.info("Бот @%s запущен — пишите ему в Telegram: https://t.me/%s", me.username, me.username)

    # Аккаунты Яндекса у каждого свои: человек подключает его сам (/login или в приложении).
    store = Storage(config.data_dir / "bot.db")
    accounts = Accounts(store, max_bitrate=config.max_bitrate)
    if imported := accounts.import_legacy_token(config.legacy_ym_token, config.legacy_users):
        log.info("Токен из YANDEX_MUSIC_TOKEN привязан к пользователям %s. Переменные YANDEX_MUSIC_TOKEN и "
                 "ALLOWED_USERS больше не нужны — их можно убрать из .env.", ", ".join(map(str, imported)))
    log.info("Подключённых аккаунтов Яндекса: %s", store.account_count())

    dp = build_dispatcher(config, bot, accounts, store)
    runner = await start_web(config, bot, accounts, store, dp["sender"])
    try:
        await bot.set_my_commands(COMMANDS)
        await setup_menu_button(bot, config)
        await setup_description(bot)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await runner.cleanup()
        await accounts.close()
        store.close()
        await bot.session.close()
