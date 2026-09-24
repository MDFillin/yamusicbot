"""Тексты ошибок для пользователя: без адресов запросов и внутренностей сервера."""

from __future__ import annotations

import aiohttp


def describe_error(e: BaseException) -> str:
    """Короткое объяснение ошибки для чата.

    В тексте ошибок aiohttp есть адрес запроса, а в адресе файла Telegram — токен бота
    (api.telegram.org/file/bot<токен>/...), поэтому такие ошибки пересказываем своими словами.
    """
    if isinstance(e, aiohttp.ClientResponseError):
        return f"сервер ответил HTTP {e.status}"
    if isinstance(e, aiohttp.ClientError | TimeoutError):
        return "сетевая ошибка, попробуйте ещё раз"
    return str(e)
