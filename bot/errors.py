"""Ошибки: тексты для пользователя (без адресов запросов и внутренностей сервера) и запись в лог."""

from __future__ import annotations

import asyncio
import functools
import logging
from collections.abc import Coroutine
from typing import Any

import aiohttp

log = logging.getLogger(__name__)


def describe_error(e: BaseException) -> str:
    """Короткое объяснение ошибки для чата.

    В тексте ошибок aiohttp есть адрес запроса, а в адресе файла Telegram — токен бота
    (api.telegram.org/file/bot<токен>/...), поэтому такие ошибки пересказываем своими словами.
    """
    if isinstance(e, aiohttp.ClientResponseError):
        return f"сервер ответил HTTP {e.status}"
    if isinstance(e, aiohttp.ClientError | TimeoutError):
        return "сетевая ошибка, попробуйте ещё раз"
    from bot.net import PROXY_ERRORS

    if isinstance(e, PROXY_ERRORS):
        return "не удалось связаться с Яндексом через прокси, попробуйте ещё раз"
    return str(e)


@functools.cache
def _expected() -> tuple[type[BaseException], ...]:
    # Импорт внутри функции: эти модули сами пользуются describe_error/log_failure.
    from aiogram.exceptions import TelegramAPIError
    from yandex_music.exceptions import YandexMusicError

    from bot.accounts import LoginError
    from bot.admin import LimitReached
    from bot.audio import ConversionError
    from bot.net import PROXY_ERRORS
    from bot.sender import TrackTooLargeError
    from bot.sources import SourceNotFoundError
    from bot.ym import TrackUnavailableError, UploadError, YandexNotReady

    return (aiohttp.ClientError, TimeoutError, TelegramAPIError, YandexMusicError, LoginError, LimitReached,
            ConversionError, TrackTooLargeError, SourceNotFoundError, TrackUnavailableError, UploadError,
            YandexNotReady, *PROXY_ERRORS)


def is_expected(e: BaseException) -> bool:
    """Сеть, Telegram, Яндекс или наша понятная ошибка — не баг бота."""
    return isinstance(e, _expected())


def log_failure(logger: logging.Logger, message: str, *args: Any, exc: BaseException) -> None:
    """Ожидаемые ошибки — одной строкой (WARNING); всё остальное скорее всего баг — с трассировкой (ERROR),
    такие записи видны в админ-панели и попадают в счётчик «Ошибок за сутки»."""
    if is_expected(exc):
        logger.warning(message + ": %r", *args, exc)
    else:
        logger.error(message, *args, exc_info=exc)


def spawn(coro: Coroutine[Any, Any, Any], name: str, keep: set[asyncio.Task] | None = None) -> asyncio.Task:
    """Фоновая задача, падение которой не потеряется: ошибка сразу попадёт в лог с трассировкой.

    keep — где хранить ссылку, пока задача идёт (иначе её может собрать сборщик мусора).
    """
    task = asyncio.create_task(coro, name=name)
    if keep is not None:
        keep.add(task)

    def finished(t: asyncio.Task) -> None:
        if keep is not None:
            keep.discard(t)
        if not t.cancelled() and (e := t.exception()) is not None:
            log.error("Фоновая задача «%s» упала", t.get_name(), exc_info=e)

    task.add_done_callback(finished)
    return task
