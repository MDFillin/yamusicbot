"""Inline-клавиатуры."""

from __future__ import annotations

from collections.abc import Sequence

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from aiogram.utils.keyboard import InlineKeyboardBuilder
from yandex_music import Playlist, Track

from bot.callbacks import CancelBulkCb, EditCb, MenuCb, NoopCb, PlaylistCb, SearchCb, TrackCb, UploadCb, ViewCb
from bot.ym import track_artists, track_title


def short(text: str, limit: int = 60) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def tracks_word(n: int) -> str:
    return f"{n} {plural(n, 'трек', 'трека', 'треков')}"


def app_button(webapp_url: str | None, text: str = "🎧 Открыть медиатеку") -> InlineKeyboardButton | None:
    return InlineKeyboardButton(text=text, web_app=WebAppInfo(url=webapp_url)) if webapp_url else None


def main_menu(webapp_url: str | None) -> InlineKeyboardMarkup:
    """Меню для того, кто уже подключил Яндекс Музыку."""
    kb = InlineKeyboardBuilder()
    rows = [1]
    if app := app_button(webapp_url):
        kb.add(app)
    else:
        rows = []
    kb.button(text="❤️ Мне нравится", callback_data=MenuCb(action="likes"))
    kb.button(text="📃 Плейлисты", callback_data=MenuCb(action="playlists"))
    kb.button(text="📊 Моя статистика", callback_data=MenuCb(action="stats"))
    kb.button(text="📌 Куда загружать", callback_data=MenuCb(action="target"))
    kb.button(text="⚙️ Настройки", callback_data=MenuCb(action="account"))
    kb.adjust(*rows, 2, 1, 2)
    return kb.as_markup()


def welcome_menu(webapp_url: str | None) -> InlineKeyboardMarkup:
    """Меню для нового пользователя: подключить Яндекс (в приложении тоже можно войти)."""
    rows = [[InlineKeyboardButton(text="🔑 Подключить Яндекс Музыку", callback_data=MenuCb(action="login").pack())]]
    if app := app_button(webapp_url, "🎧 Открыть приложение"):
        rows.append([app])
    rows.append([InlineKeyboardButton(text="❓ Что умеет бот", callback_data=MenuCb(action="help").pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


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


MAX_EDIT_BUTTONS = 8


def upload_card(
    labels: Sequence[tuple[int, str]], playlists: Sequence[Playlist], target: Playlist | None,
) -> InlineKeyboardMarkup:
    """Очередь загрузки: правка каждого файла и выбор плейлиста (или загрузка в плейлист по умолчанию)."""
    rows: list[list[InlineKeyboardButton]] = []
    if target is not None:
        rows.append([InlineKeyboardButton(text=short(f"⬆️ Загрузить в «{target.title}»"),
                                          callback_data=UploadCb(action="to", kind=target.kind).pack())])
    single = len(labels) == 1
    for n, (pid, label) in enumerate(labels[:MAX_EDIT_BUTTONS], 1):
        text = "✏️ Изменить данные трека" if single else short(f"✏️ {n}. {label}", 48)
        rows.append([InlineKeyboardButton(text=text, callback_data=EditCb(action="open", pid=pid).pack())])
    if target is None:
        for p in playlists:
            rows.append([InlineKeyboardButton(text=short(f"📤 {playlist_label(p)}"),
                                              callback_data=UploadCb(action="to", kind=p.kind).pack())])
        rows.append([InlineKeyboardButton(text="➕ Новый плейлист", callback_data=UploadCb(action="new").pack())])
        rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data=UploadCb(action="cancel").pack())])
    else:
        rows.append([
            InlineKeyboardButton(text="📃 Другой плейлист", callback_data=UploadCb(action="pick").pack()),
            InlineKeyboardButton(text="❌ Отмена", callback_data=UploadCb(action="cancel").pack()),
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def track_editor(pid: int, has_cover: bool) -> InlineKeyboardMarkup:
    def b(text: str, action: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(text=text, callback_data=EditCb(action=action, pid=pid).pack())

    cover_row = [b("🖼 Сменить обложку" if has_cover else "🖼 Добавить обложку", "cover")]
    if has_cover:
        cover_row.append(b("🗑 Убрать обложку", "nocover"))
    return InlineKeyboardMarkup(inline_keyboard=[
        [b("🎵 Название", "title"), b("👤 Исполнитель", "artist")],
        [b("💿 Альбом", "album"), b("📅 Год", "year")],
        cover_row,
        [b("✅ Готово", "done")],
    ])


def target_menu(playlists: Sequence[Playlist], has_target: bool) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for p in playlists:
        kb.button(text=short(f"📌 {playlist_label(p)}"), callback_data=PlaylistCb(action="target", kind=p.kind))
    if has_target:
        kb.button(text="🚫 Без плейлиста по умолчанию", callback_data=PlaylistCb(action="untarget"))
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
