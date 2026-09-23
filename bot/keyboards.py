"""Inline-клавиатуры."""

from __future__ import annotations

from collections.abc import Sequence

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from yandex_music import Playlist, Track

from bot.callbacks import CancelBulkCb, NoopCb, PlaylistCb, SearchCb, TrackCb, UploadCb, ViewCb
from bot.ym import track_artists, track_title


def short(text: str, limit: int = 60) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def fmt_duration(ms: int | None) -> str:
    if not ms:
        return ""
    seconds = ms // 1000
    return f"{seconds // 60}:{seconds % 60:02d}"


def track_label(track: Track) -> str:
    return f"{track_artists(track)} — {track_title(track)}"


def playlist_label(playlist: Playlist) -> str:
    return f"{playlist.title} ({playlist.track_count or 0})"


def track_actions(track_id: str, liked: bool = False) -> InlineKeyboardMarkup:
    like = (
        InlineKeyboardButton(text="💔 Убрать из любимых", callback_data=TrackCb(action="unlike", track=track_id).pack())
        if liked
        else InlineKeyboardButton(text="❤️ В любимые", callback_data=TrackCb(action="like", track=track_id).pack())
    )
    add = InlineKeyboardButton(text="➕ В плейлист", callback_data=TrackCb(action="add", track=track_id).pack())
    return InlineKeyboardMarkup(inline_keyboard=[[like, add]])


def pager(src: str, ref: str, page: int, pages: int) -> list[InlineKeyboardButton]:
    row = []
    if page > 0:
        row.append(InlineKeyboardButton(text="◀️", callback_data=ViewCb(src=src, ref=ref, page=page - 1).pack()))
    row.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data=NoopCb().pack()))
    if page < pages - 1:
        row.append(InlineKeyboardButton(text="▶️", callback_data=ViewCb(src=src, ref=ref, page=page + 1).pack()))
    return row


def playlists_menu(playlists: Sequence[Playlist], uid: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for p in playlists:
        kb.button(text=short(f"📃 {playlist_label(p)}"), callback_data=ViewCb(src="pl", ref=f"{uid}.{p.kind}"))
    kb.button(text="❤️ Мне нравится", callback_data=ViewCb(src="likes"))
    kb.button(text="➕ Новый плейлист", callback_data=PlaylistCb(action="new"))
    kb.adjust(1)
    return kb.as_markup()


def pick_playlist(playlists: Sequence[Playlist], action: str, track: str = "") -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for p in playlists:
        kb.button(text=short(playlist_label(p)), callback_data=PlaylistCb(action=action, kind=p.kind, track=track))
    kb.adjust(1)
    return kb.as_markup()


def upload_targets(playlists: Sequence[Playlist]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for p in playlists:
        kb.button(text=short(f"📤 {playlist_label(p)}"), callback_data=UploadCb(action="to", kind=p.kind))
    kb.button(text="➕ Новый плейлист", callback_data=UploadCb(action="new"))
    kb.button(text="❌ Отмена", callback_data=UploadCb(action="cancel"))
    kb.adjust(1)
    return kb.as_markup()


def target_menu(playlists: Sequence[Playlist], has_target: bool) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for p in playlists:
        kb.button(text=short(f"📌 {playlist_label(p)}"), callback_data=PlaylistCb(action="target", kind=p.kind))
    if has_target:
        kb.button(text="🚫 Спрашивать каждый раз", callback_data=PlaylistCb(action="untarget"))
    kb.adjust(1)
    return kb.as_markup()


def search_tabs(active: str) -> list[InlineKeyboardButton]:
    tabs = [("track", "🎵 Треки"), ("album", "💿 Альбомы"), ("playlist", "📃 Плейлисты"), ("artist", "👤 Артисты")]
    return [
        InlineKeyboardButton(text=f"• {label} •" if t == active else label, callback_data=SearchCb(type=t).pack())
        for t, label in tabs
    ]


def cancel_bulk() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⛔ Остановить", callback_data=CancelBulkCb().pack())]]
    )
