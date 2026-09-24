"""Отправка треков из Яндекс Музыки в Telegram."""

from __future__ import annotations

import asyncio
import contextlib
import html
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, TypeVar

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup, Message
from yandex_music import Track

from bot.config import Config
from bot.keyboards import track_actions
from bot.settings import get_quality
from bot.storage import Storage
from bot.ym import YandexMusic, tagged_filename, track_album, track_artists, track_title

if TYPE_CHECKING:
    from bot.admin import Admin

log = logging.getLogger(__name__)
T = TypeVar("T")
PARALLEL_DOWNLOADS = 4  # треков, которые сервер одновременно качает из Яндекса и отправляет (каждый — в памяти)


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
    def __init__(self, bot: Bot, store: Storage, config: Config, admin: Admin | None = None) -> None:
        self._bot = bot
        self._store = store
        self._config = config
        self._admin = admin  # дневные лимиты и статистика скачиваний
        self._slots = asyncio.Semaphore(PARALLEL_DOWNLOADS)  # бот открыт всем: очередь, а не десятки файлов сразу

    async def send(self, chat_id: int, track: Track, ym: YandexMusic, user_id: int | None = None) -> Message:
        """Отправляет трек из аккаунта ym; повторно тот же трек уходит по file_id без скачивания.

        Качество — из настроек пользователя (в личном чате с ботом его ID совпадает с chat_id).
        """
        user_id = user_id or chat_id
        track_id = str(track.id)
        markup = track_actions(track_id)
        caption = track_caption(track)
        quality = get_quality(self._store, self._config, user_id, "download")
        # Кэш — на аккаунт Яндекса и качество: без Плюса Яндекс отдаёт другой файл (отрывок).
        account = ym.uid or 0
        if self._admin is not None:
            self._admin.check_limit(user_id, "download")

        msg = None
        cached = self._store.get_file_id(account, track_id, quality)
        if cached:
            try:
                msg = await retry_telegram(
                    lambda: self._bot.send_audio(chat_id, cached, caption=caption, reply_markup=markup)
                )
            except TelegramBadRequest:
                log.info("file_id для %s устарел, скачиваю заново", track_id)
        if msg is None:
            async with self._slots:
                msg = await self._upload(chat_id, track, ym, quality, account, caption, markup)
        if self._admin is not None:
            self._admin.count(user_id, "download")
        return msg

    async def audio_file_id(self, user_id: int, track: Track, ym: YandexMusic) -> str:
        """file_id трека для инлайн-режима (туда можно вставить только уже загруженный в Telegram файл).

        Если трека нет в кэше, он отправляется в личный чат с ботом без звука и сразу удаляется оттуда.
        """
        track_id = str(track.id)
        quality = get_quality(self._store, self._config, user_id, "download")
        account = ym.uid or 0
        if self._admin is not None:
            self._admin.check_limit(user_id, "download")
        file_id = self._store.get_file_id(account, track_id, quality)
        if file_id is None:
            async with self._slots:
                msg = await self._upload(user_id, track, ym, quality, account, "", None, silent=True)
            with contextlib.suppress(TelegramBadRequest):
                await msg.delete()
            if msg.audio is None:
                raise TrackTooLargeError("Telegram не принял файл как аудио")
            file_id = msg.audio.file_id
        if self._admin is not None:
            self._admin.count(user_id, "download")
        return file_id

    async def _upload(self, chat_id: int, track: Track, ym: YandexMusic, quality: int, account: int,
                      caption: str, markup: InlineKeyboardMarkup | None, silent: bool = False) -> Message:
        track_id = str(track.id)
        (data, bitrate), thumb = await asyncio.gather(
            ym.download_tagged(track, quality), ym.download_cover(track, "200x200"),
        )
        if len(data) > self._config.max_tg_upload:
            raise TrackTooLargeError(
                f"Файл {len(data) // (1024 * 1024)} МБ — больше лимита Telegram для ботов "
                f"({self._config.max_tg_upload // (1024 * 1024)} МБ)"
            )

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
                disable_notification=silent,
                request_timeout=600,
            )
        )
        if msg.audio:
            self._store.set_file_id(account, track_id, quality, msg.audio.file_id)
        log.info("Отправлен трек %s (%s kbps)", track_id, bitrate)
        return msg
