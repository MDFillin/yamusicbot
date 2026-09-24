"""Инлайн-режим: «@бот запрос» в любом чате — найти трек в своей Яндекс Музыке и отправить собеседнику.

Пустой запрос показывает «Мне нравится», ссылка music.yandex.* — трек, альбом, плейлист или артиста.
Трек, который уже отправлялся, уходит сразу (по file_id). Остальные сначала появляются в чате как
«⏳ Загружаю…», а когда Telegram сообщит о выборе (chosen_inline_result — включается в @BotFather,
/setinlinefeedback), бот скачивает трек и подменяет сообщение аудиофайлом.
"""

from __future__ import annotations

import contextlib
import html
import logging
import re

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    ChosenInlineResult,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQuery,
    InlineQueryResultArticle,
    InlineQueryResultCachedAudio,
    InlineQueryResultsButton,
    InputMediaAudio,
    InputTextMessageContent,
)
from yandex_music import Track

from bot.admin import Admin
from bot.callbacks import NoopCb
from bot.config import Config
from bot.errors import describe_error
from bot.keyboards import fmt_duration
from bot.links import parse_link
from bot.sender import TrackSender, track_caption
from bot.settings import get_quality
from bot.sources import SourceNotFoundError, load_source, playlist_ref, resolve
from bot.storage import Storage
from bot.ym import YandexMusic, cover_url, track_artists, track_title

log = logging.getLogger(__name__)
router = Router(name="inline")

PAGE = 20
MAX_PAGES = 10
_START_ARG = re.compile(r"[\w-]{1,60}")


def _listen_button(username: str | None, track_id: str) -> InlineKeyboardMarkup | None:
    """Кнопка под треком: открыть бота (и сразу получить этот трек в личке)."""
    if not username:
        return None
    arg = f"t{track_id}" if _START_ARG.fullmatch(track_id) else "start"
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🎧 Слушать в боте", url=f"https://t.me/{username}?start={arg}"),
    ]])


async def find_tracks(ym: YandexMusic, text: str, page: int) -> tuple[list[Track], bool]:
    """Треки для инлайн-запроса и есть ли следующая страница."""
    if not text:
        ids = await ym.get_liked_track_ids()
        chunk = ids[page * PAGE:(page + 1) * PAGE]
        return await resolve(ym, list(chunk)), len(ids) > (page + 1) * PAGE

    if (link := parse_link(text)) is not None:
        if link.kind == "track":
            track = await ym.get_track(link.track) if page == 0 else None
            return ([track] if track else []), False
        if link.kind == "playlist_uuid":
            playlist = await ym.get_playlist_by_uuid(link.uuid)
            if playlist is None:
                return [], False
            src, ref = "pl", playlist_ref(playlist)
        else:
            src, ref = {"album": ("alb", link.album), "playlist": ("pl", f"{link.owner}.{link.playlist_kind}"),
                        "artist": ("art", link.artist)}[link.kind]
        try:
            source = await load_source(ym, src, ref)
        except SourceNotFoundError:
            return [], False
        items = source.items[page * PAGE:(page + 1) * PAGE]
        return await resolve(ym, items), len(source.items) > (page + 1) * PAGE

    result = await ym.search(text, "track", page=page)
    tracks = list(result.tracks.results) if result and result.tracks and result.tracks.results else []
    total = result.tracks.total if result and result.tracks and result.tracks.total else 0
    return tracks, bool(tracks) and (page + 1) * len(tracks) < total


def build_result(track: Track, file_id: str | None, username: str | None):
    track_id = str(track.id)
    markup = _listen_button(username, track_id)
    if file_id:  # уже есть в Telegram — отправится мгновенно
        return InlineQueryResultCachedAudio(id=f"c:{track_id}"[:64], audio_file_id=file_id,
                                            caption=track_caption(track), reply_markup=markup)
    title, artists = track_title(track), track_artists(track)
    duration = fmt_duration(track.duration_ms)
    placeholder = f"⏳ Загружаю «{html.escape(title)}» — {html.escape(artists)}…"
    return InlineQueryResultArticle(
        id=f"t:{track_id}"[:64],
        title=title,
        description=" · ".join(x for x in (artists, duration) if x),
        thumbnail_url=cover_url(track.cover_uri, "200x200"),
        input_message_content=InputTextMessageContent(message_text=placeholder),
        # Без кнопки Telegram не пришлёт inline_message_id, и сообщение нельзя будет заменить аудиофайлом.
        reply_markup=markup or InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="⏳", callback_data=NoopCb().pack())]]),
    )


@router.inline_query()
async def on_inline(query: InlineQuery, bot: Bot, ym: YandexMusic, store: Storage, config: Config,
                    admin: Admin) -> None:
    user_id = query.from_user.id
    if admin.left(user_id, "download") == 0:
        button = InlineQueryResultsButton(text="⛔ Лимит скачиваний на сегодня исчерпан", start_parameter="start")
        await query.answer([], cache_time=60, is_personal=True, button=button)
        return
    text = query.query.strip()[:200]
    page = int(query.offset) if query.offset.isdigit() else 0
    tracks, more = await find_tracks(ym, text, page) if page < MAX_PAGES else ([], False)
    username = (await bot.me()).username
    quality = get_quality(store, config, user_id, "download")
    results = []
    for track in tracks:
        if track.available is False or len(str(track.id)) > 60:
            continue
        results.append(build_result(track, store.get_file_id(ym.uid or 0, str(track.id), quality), username))
    button = None
    if not text and page == 0:
        button = InlineQueryResultsButton(text="❤️ Мне нравится · введите запрос для поиска", start_parameter="start")
    await query.answer(results, cache_time=10, is_personal=True, next_offset=str(page + 1) if more else "",
                       button=button)


@router.chosen_inline_result(F.result_id.startswith("c:"))
async def on_chosen_cached(result: ChosenInlineResult, admin: Admin) -> None:
    admin.count(result.from_user.id, "download")  # трек ушёл из кэша, но в статистике это тоже скачивание


@router.chosen_inline_result(F.result_id.startswith("t:"))
async def on_chosen(result: ChosenInlineResult, bot: Bot, ym: YandexMusic, sender: TrackSender) -> None:
    if not result.inline_message_id:
        return
    track_id = result.result_id[2:]
    username = (await bot.me()).username
    markup = _listen_button(username, track_id)
    try:
        track = await ym.get_track(track_id)
        if track is None:
            raise LookupError("трек не найден")
        file_id = await sender.audio_file_id(result.from_user.id, track, ym)
        await bot.edit_message_media(
            inline_message_id=result.inline_message_id,
            media=InputMediaAudio(media=file_id, caption=track_caption(track)),
            reply_markup=markup,
        )
    except Exception as e:
        log.warning("Инлайн: не удалось отправить трек %s: %s", track_id, e)
        with contextlib.suppress(TelegramBadRequest):
            await bot.edit_message_text(
                inline_message_id=result.inline_message_id,
                text=f"😕 Не получилось отправить трек: {html.escape(describe_error(e))}",
                reply_markup=markup,
            )
