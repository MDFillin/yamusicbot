"""Списки треков (лайки, плейлисты, альбомы, артисты) и их постраничный вывод."""

from __future__ import annotations

import html
import math
from dataclasses import dataclass, field

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from yandex_music import Playlist, Track

from bot.callbacks import BulkCb, PlaylistCb, TrackCb, ViewCb
from bot.keyboards import fmt_duration, pager, short, track_label, tracks_word
from bot.ym import YandexMusic

PAGE_SIZE = 8

# Элемент списка: либо полный Track, либо его id, который докачается при показе страницы.
Item = Track | str


class SourceNotFoundError(RuntimeError):
    pass


@dataclass
class TrackSource:
    title: str
    ref: str = ""  # нормализованная ссылка на источник для кнопок (для плейлиста — "<uid>.<kind>")
    items: list[Item] = field(default_factory=list)
    own_playlist: Playlist | None = None  # свой плейлист: можно удалить/загружать в него
    cover: str | None = None  # шаблон адреса обложки (avatars.yandex.net/...%%)
    name: str = ""  # название без значков — для мини-приложения
    subtitle: str = ""


def playlist_cover(playlist: Playlist) -> str | None:
    cover = playlist.cover
    if cover and cover.uri:
        return cover.uri
    if cover and cover.items_uri:
        return cover.items_uri[0]
    return playlist.og_image


def playlist_ref(playlist: Playlist) -> str:
    uid = playlist.owner.uid if playlist.owner and playlist.owner.uid else playlist.uid
    return f"{uid}.{playlist.kind}"


def _playlist_items(playlist: Playlist) -> list[Item]:
    return [s.track or s.track_id for s in playlist.tracks or []]


async def load_source(ym: YandexMusic, src: str, ref: str) -> TrackSource:
    if src == "likes":
        return TrackSource("❤️ Мне нравится", "", list(await ym.get_liked_track_ids()), name="Мне нравится")

    if src == "pl":
        owner, _, kind = ref.partition(".")
        playlist = await ym.get_playlist(kind, owner)
        if playlist is None:
            raise SourceNotFoundError("Плейлист не найден")
        ref = playlist_ref(playlist)
        own = playlist if ref.partition(".")[0] == str(ym.uid) else None
        owner_name = (playlist.owner.name or playlist.owner.login) if playlist.owner else None
        return TrackSource(
            f"📃 {playlist.title}", ref, _playlist_items(playlist), own, playlist_cover(playlist),
            name=playlist.title, subtitle="Мой плейлист" if own else f"Плейлист · {owner_name or 'Яндекс Музыка'}",
        )

    if src == "alb":
        album = await ym.get_album(ref)
        if album is None:
            raise SourceNotFoundError("Альбом не найден")
        artists = ", ".join(a.name for a in album.artists or [] if a.name)
        tracks = [t for volume in album.volumes or [] for t in volume]
        year = f" ({album.year})" if album.year else ""
        return TrackSource(
            f"💿 {artists} — {album.title}{year}", ref, list(tracks), cover=album.cover_uri,
            name=album.title, subtitle=f"Альбом · {artists}{year}",
        )

    if src == "art":
        tracks = await ym.get_artist_tracks(ref)
        artist = next((a for t in tracks for a in t.artists or [] if str(a.id) == ref), None)
        if artist is None and tracks and tracks[0].artists:
            artist = tracks[0].artists[0]
        name = artist.name if artist else "Артист"
        cover = artist.cover.uri if artist and artist.cover else None
        return TrackSource(
            f"👤 {name}: популярные треки", ref, list(tracks), cover=cover, name=name, subtitle="Популярные треки",
        )

    raise SourceNotFoundError(f"Неизвестный источник {src}")


async def resolve(ym: YandexMusic, items: list[Item]) -> list[Track]:
    ids = [i for i in items if isinstance(i, str)]
    fetched = {str(t.id): t for t in await ym.get_tracks(ids)} if ids else {}
    result = []
    for item in items:
        track = fetched.get(item.split(":")[0]) if isinstance(item, str) else item
        if track is not None:
            result.append(track)
    return result


async def render_page(ym: YandexMusic, source: TrackSource, src: str, page: int) -> tuple[str, InlineKeyboardMarkup]:
    ref = source.ref
    total = len(source.items)
    pages = max(1, math.ceil(total / PAGE_SIZE))
    page = min(max(page, 0), pages - 1)
    tracks = await resolve(ym, source.items[page * PAGE_SIZE:(page + 1) * PAGE_SIZE])

    info = " · ".join(x for x in (source.subtitle, tracks_word(total)) if x)
    text = f"<b>{html.escape(source.title)}</b>\n{html.escape(info)}"
    text += "\n\nНажмите на трек — пришлю MP3." if total else "\n\nЗдесь пока пусто."

    rows: list[list[InlineKeyboardButton]] = []
    for track in tracks:
        duration = fmt_duration(track.duration_ms)
        label = short(f"⬇️ {track_label(track)}" + (f" · {duration}" if duration else ""))
        rows.append([InlineKeyboardButton(text=label, callback_data=TrackCb(action="dl", track=str(track.id)).pack())])
    if pages > 1:
        rows.append(pager(src, ref, page, pages))
    if total:
        bulk = BulkCb(src=src, ref=ref).pack()
        rows.append([InlineKeyboardButton(text=f"⬇️ Скачать всё ({total})", callback_data=bulk)])
    if source.own_playlist is not None:
        kind = source.own_playlist.kind
        target = PlaylistCb(action="target", kind=kind).pack()
        delete = PlaylistCb(action="delete", kind=kind).pack()
        rows.append([InlineKeyboardButton(text="📌 Загружать сюда мои файлы", callback_data=target)])
        rows.append([InlineKeyboardButton(text="🗑 Удалить плейлист", callback_data=delete)])
    if source.own_playlist is not None or src == "likes":
        rows.append([InlineKeyboardButton(text="⬅️ Мои плейлисты", callback_data=ViewCb(src="pls").pack())])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)
