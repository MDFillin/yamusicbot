from __future__ import annotations

import html
import logging
import sys
from typing import Any

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, ErrorEvent, MenuButtonDefault, MenuButtonWebApp, WebAppInfo
from aiohttp import web
from yandex_music.exceptions import UnauthorizedError, YandexMusicError

from bot.audio import ffmpeg_available
from bot.config import Config, ConfigError, load_config
from bot.handlers import build_router
from bot.handlers.upload import UploadQueue
from bot.middlewares import AccessMiddleware
from bot.sender import TrackSender
from bot.storage import Storage
from bot.web.app import create_app
from bot.ym import YandexMusic

log = logging.getLogger("bot")

COMMANDS = [
    BotCommand(command="app", description="🎧 Открыть медиатеку"),
    BotCommand(command="likes", description="❤️ Мне нравится"),
    BotCommand(command="playlists", description="📃 Мои плейлисты"),
    BotCommand(command="target", description="📌 Куда загружать мои файлы"),
    BotCommand(command="newplaylist", description="➕ Создать плейлист"),
    BotCommand(command="help", description="❓ Справка"),
    BotCommand(command="cancel", description="Отменить действие"),
]


def build_bot(config: Config) -> Bot:
    session = None
    if config.bot_api_url:
        session = AiohttpSession(api=TelegramAPIServer.from_base(config.bot_api_url, is_local=config.bot_api_local))
    return Bot(config.bot_token, session=session, default=DefaultBotProperties(parse_mode=ParseMode.HTML))


def build_dependencies(config: Config, bot: Bot, ym: YandexMusic, store: Storage) -> dict[str, Any]:
    """Объекты, которые aiogram подставляет в обработчики по имени аргумента."""
    return {
        "config": config,
        "ym": ym,
        "store": store,
        "sender": TrackSender(bot, ym, store, config),
        "uploads": UploadQueue(),
    }


def build_dispatcher(config: Config, bot: Bot, ym: YandexMusic, store: Storage) -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage(), **build_dependencies(config, bot, ym, store))

    access = AccessMiddleware(config.allowed_users)
    dp.message.outer_middleware(access)
    dp.callback_query.outer_middleware(access)
    dp.include_router(build_router())

    @dp.errors()
    async def on_error(event: ErrorEvent) -> None:
        log.exception("Ошибка при обработке апдейта", exc_info=event.exception)
        text = f"⚠️ Ошибка: {html.escape(type(event.exception).__name__)}: {html.escape(str(event.exception))[:500]}"
        update = event.update
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


async def start_web(config: Config, bot: Bot, ym: YandexMusic, store: Storage, sender: TrackSender) -> web.AppRunner:
    runner = web.AppRunner(create_app(config, bot, ym, store, sender), access_log=None)
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


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        config = load_config()
    except ConfigError as e:
        sys.exit(f"Ошибка настройки: {e}")
    if not config.allowed_users:
        log.warning("ALLOWED_USERS пуст — бот никого не пустит. Напишите боту, он покажет ваш ID.")
    if not ffmpeg_available():
        log.warning("ffmpeg не найден: не-MP3 файлы будут загружаться без конвертации")

    ym = YandexMusic(config.ym_token, max_bitrate=config.max_bitrate)
    try:
        await ym.start()
    except UnauthorizedError as e:
        # Библиотека выдаёт это и на 401, и на 403: второй бывает из-за блокировки по IP, а не токена.
        sys.exit(
            f"Яндекс Музыка ответила отказом ({e}). Либо YANDEX_MUSIC_TOKEN неверный/просрочен "
            "(получите новый: python -m bot.get_token), либо Яндекс не пускает запросы с IP этого сервера."
        )
    except YandexMusicError as e:
        sys.exit(f"Не удалось подключиться к Яндекс Музыке: {e}")
    log.info("Яндекс Музыка: %s (uid %s), Плюс: %s", ym.login, ym.uid, ym.has_plus)

    bot = build_bot(config)
    store = Storage(config.data_dir / "storage.json")
    dp = build_dispatcher(config, bot, ym, store)
    runner = await start_web(config, bot, ym, store, dp["sender"])
    try:
        await bot.set_my_commands(COMMANDS)
        await setup_menu_button(bot, config)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await runner.cleanup()
        await ym.close()
        await bot.session.close()
