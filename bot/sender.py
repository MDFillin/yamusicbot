"""Отправка треков из Яндекс Музыки в Telegram."""

from __future__ import annotations

import asyncio
import html
import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import BufferedInputFile, Message
from yandex_music import Track

from bot.config import Config
from bot.keyboards import track_actions
from bot.storage import Storage
from bot.ym import YandexMusic, tagged_filename, track_album, track_artists, track_title

log = logging.getLogger(__name__)
T = TypeVar("T")


class TrackTooLargeError(RuntimeError):
    pass


async def retry_telegram(call: Callable[[], Awaitable[T]], attempts: int = 5) -> T:
    """Повторяет запрос к Telegram, если он попросил подождать (flood control)."""
    for attempt in range(attempts):
        try:
            return await call()
        except TelegramRetryAfter as e:
            if attempt == attempts - 1:
                raise
            await asyncio.sleep(e.retry_after + 1)
    raise AssertionError("unreachable")


def track_caption(track: Track) -> str:
    album = track_album(track)
    if album is None or not album.title:
        return ""
    year = f", {album.year}" if album.year else ""
    return f"💿 {html.escape(album.title)}{year}"


class TrackSender:
    def __init__(self, bot: Bot, ym: YandexMusic, store: Storage, config: Config) -> None:
        self._bot = bot
        self._ym = ym
        self._store = store
        self._config = config

    async def send(self, chat_id: int, track: Track) -> Message:
        track_id = str(track.id)
        markup = track_actions(track_id)
        caption = track_caption(track)

        cached = self._store.get_file_id(track_id, self._config.max_bitrate)
        if cached:
            try:
                return await retry_telegram(
                    lambda: self._bot.send_audio(chat_id, cached, caption=caption, reply_markup=markup)
                )
            except TelegramBadRequest:
                log.info("file_id для %s устарел, скачиваю заново", track_id)

        data, bitrate = await self._ym.download_tagged(track)
        if len(data) > self._config.max_tg_upload:
            raise TrackTooLargeError(
                f"Файл {len(data) // (1024 * 1024)} МБ — больше лимита Telegram для ботов "
                f"({self._config.max_tg_upload // (1024 * 1024)} МБ)"
            )
        thumb = await self._ym.download_cover(track, "200x200")

        msg = await retry_telegram(
            lambda: self._bot.send_audio(
                chat_id,
                BufferedInputFile(data, filename=tagged_filename(track)),
                caption=caption,
                title=track_title(track),
                performer=track_artists(track),
                duration=(track.duration_ms or 0) // 1000 or None,
                thumbnail=BufferedInputFile(thumb, filename="cover.jpg") if thumb else None,
                reply_markup=markup,
                request_timeout=600,
            )
        )
        if msg.audio:
            self._store.set_file_id(track_id, self._config.max_bitrate, msg.audio.file_id)
        log.info("Отправлен трек %s (%s kbps)", track_id, bitrate)
        return msg
