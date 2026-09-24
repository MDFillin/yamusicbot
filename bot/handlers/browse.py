"""Поиск, ссылки, лайки, плейлисты и действия с треками."""

from __future__ import annotations

import html
import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.callbacks import PlaylistCb, SearchCb, TrackCb, ViewCb
from bot.keyboards import (
    fmt_duration,
    pick_playlist,
    playlists_menu,
    search_tabs,
    short,
    target_menu,
    track_actions,
    track_label,
)
from bot.links import YMLink, parse_link
from bot.sender import TrackSender
from bot.sources import SourceNotFoundError, load_source, playlist_ref, render_page
from bot.states import NewPlaylist
from bot.storage import Storage
from bot.ym import YandexMusic, track_title

log = logging.getLogger(__name__)
router = Router(name="browse")

SEARCH_LIMIT = 10


async def _edit(message: Message, text: str, markup: InlineKeyboardMarkup | None) -> None:
    try:
        await message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise


# ---------- списки треков ----------

async def show_source(
    target: Message, ym: YandexMusic, src: str, ref: str = "", page: int = 0, edit: bool = False
) -> None:
    try:
        source = await load_source(ym, src, ref)
    except SourceNotFoundError as e:
        await target.answer(f"😕 {html.escape(str(e))}")
        return
    text, markup = await render_page(ym, source, src, page)
    if edit:
        await _edit(target, text, markup)
    else:
        await target.answer(text, reply_markup=markup)


async def playlists_text(ym: YandexMusic) -> tuple[str, InlineKeyboardMarkup]:
    playlists = await ym.get_my_playlists()
    text = "<b>📃 Ваши плейлисты</b>" if playlists else "<b>📃 Плейлистов пока нет</b>"
    return text, playlists_menu(playlists, ym.uid or 0)


@router.message(Command("likes"))
async def cmd_likes(message: Message, ym: YandexMusic) -> None:
    await show_source(message, ym, "likes")


@router.message(Command("playlists"))
async def cmd_playlists(message: Message, ym: YandexMusic) -> None:
    text, markup = await playlists_text(ym)
    await message.answer(text, reply_markup=markup)


@router.callback_query(ViewCb.filter())
async def on_view(call: CallbackQuery, callback_data: ViewCb, ym: YandexMusic) -> None:
    await call.answer()
    if callback_data.src == "pls":
        text, markup = await playlists_text(ym)
        await _edit(call.message, text, markup)
        return
    await show_source(call.message, ym, callback_data.src, callback_data.ref, callback_data.page, edit=True)


# ---------- ссылки и поиск ----------

async def open_link(message: Message, link: YMLink, ym: YandexMusic, sender: TrackSender) -> None:
    if link.kind == "track":
        track = await ym.get_track(link.track)
        if track is None:
            await message.answer("😕 Трек не найден")
            return
        status = await message.answer(f"⏳ Скачиваю «{html.escape(track_title(track))}»…")
        await sender.send(message.chat.id, track, ym)
        await status.delete()
    elif link.kind == "album":
        await show_source(message, ym, "alb", link.album)
    elif link.kind == "playlist":
        await show_source(message, ym, "pl", f"{link.owner}.{link.playlist_kind}")
    elif link.kind == "playlist_uuid":
        playlist = await ym.get_playlist_by_uuid(link.uuid)
        if playlist is None:
            await message.answer("😕 Плейлист не найден")
            return
        await show_source(message, ym, "pl", playlist_ref(playlist))
    elif link.kind == "artist":
        await show_source(message, ym, "art", link.artist)


async def render_search(ym: YandexMusic, query: str, type_: str) -> tuple[str, InlineKeyboardMarkup]:
    result = await ym.search(query, type_)
    rows: list[list[InlineKeyboardButton]] = [search_tabs(type_)]
    header = f"🔎 <b>{html.escape(query)}</b>"
    if result and result.misspell_corrected and result.misspell_result:
        header += f"\nИсправил на: <i>{html.escape(result.misspell_result)}</i>"

    def button(text: str, data: str) -> list[InlineKeyboardButton]:
        return [InlineKeyboardButton(text=short(text), callback_data=data)]

    if type_ == "track" and result and result.tracks:
        for t in result.tracks.results[:SEARCH_LIMIT]:
            duration = fmt_duration(t.duration_ms)
            rows.append(button(f"⬇️ {track_label(t)}" + (f" · {duration}" if duration else ""),
                               TrackCb(action="dl", track=str(t.id)).pack()))
    elif type_ == "album" and result and result.albums:
        for a in result.albums.results[:SEARCH_LIMIT]:
            artists = ", ".join(x.name for x in a.artists or [] if x.name)
            year = f" ({a.year})" if a.year else ""
            rows.append(button(f"💿 {artists} — {a.title}{year}", ViewCb(src="alb", ref=str(a.id)).pack()))
    elif type_ == "playlist" and result and result.playlists:
        for p in result.playlists.results[:SEARCH_LIMIT]:
            rows.append(button(f"📃 {p.title} ({p.track_count or 0})", ViewCb(src="pl", ref=playlist_ref(p)).pack()))
    elif type_ == "artist" and result and result.artists:
        for a in result.artists.results[:SEARCH_LIMIT]:
            rows.append(button(f"👤 {a.name}", ViewCb(src="art", ref=str(a.id)).pack()))

    if len(rows) == 1:
        header += "\n\nНичего не нашлось 😕"
    return header, InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(StateFilter(None), F.text, ~F.text.startswith("/"))
async def on_text(message: Message, state: FSMContext, ym: YandexMusic, sender: TrackSender) -> None:
    link = parse_link(message.text)
    if link is not None:
        await open_link(message, link, ym, sender)
        return
    query = message.text.strip()[:200]
    await state.update_data(query=query)
    text, markup = await render_search(ym, query, "track")
    await message.answer(text, reply_markup=markup)


@router.callback_query(SearchCb.filter())
async def on_search_tab(call: CallbackQuery, callback_data: SearchCb, state: FSMContext, ym: YandexMusic) -> None:
    query = (await state.get_data()).get("query")
    if not query:
        await call.answer("Поиск устарел, отправьте запрос ещё раз")
        return
    await call.answer()
    text, markup = await render_search(ym, query, callback_data.type)
    await _edit(call.message, text, markup)


# ---------- действия с треком ----------

@router.callback_query(TrackCb.filter(F.action.in_({"like", "unlike"})))
async def on_like(call: CallbackQuery, callback_data: TrackCb, ym: YandexMusic) -> None:
    liked = callback_data.action == "like"
    if liked:
        await ym.like(callback_data.track)
    else:
        await ym.unlike(callback_data.track)
    await call.answer("❤️ Добавлено в «Мне нравится»" if liked else "Убрано из «Мне нравится»")
    await call.message.edit_reply_markup(reply_markup=track_actions(callback_data.track, liked=liked))


@router.callback_query(TrackCb.filter(F.action == "add"))
async def on_add(call: CallbackQuery, callback_data: TrackCb, ym: YandexMusic) -> None:
    await call.answer()
    playlists = await ym.get_my_playlists()
    if not playlists:
        await call.message.answer("У вас пока нет плейлистов. Создайте: /newplaylist название")
        return
    await call.message.answer("В какой плейлист добавить трек?",
                              reply_markup=pick_playlist(playlists, "insert", callback_data.track))


@router.callback_query(PlaylistCb.filter(F.action == "insert"))
async def on_insert(call: CallbackQuery, callback_data: PlaylistCb, ym: YandexMusic) -> None:
    track = await ym.get_track(callback_data.track)
    if track is None:
        await call.answer("Трек не найден", show_alert=True)
        return
    playlist = await ym.add_to_playlist(callback_data.kind, track)
    await call.answer()
    title = html.escape(playlist.title) if playlist else "плейлист"
    await _edit(call.message, f"✅ «{html.escape(track_title(track))}» добавлен в «{title}»", None)


# ---------- управление плейлистами ----------

async def target_prompt(ym: YandexMusic, store: Storage, user_id: int) -> tuple[str, InlineKeyboardMarkup | None]:
    playlists = await ym.get_my_playlists()
    current = store.get_upload_target(user_id)
    name = next((p.title for p in playlists if p.kind == current), None)
    text = (f"📌 Плейлист по умолчанию для загрузки: «{html.escape(name)}». Присланные файлы бот предложит "
            "загрузить туда одной кнопкой (перед этим можно поправить данные трека)." if name
            else "📌 Плейлист по умолчанию не выбран: для каждой пачки файлов бот спрашивает, куда их загрузить.")
    if not playlists:
        return text + "\n\nПлейлистов пока нет: /newplaylist название", None
    return text + "\n\nВыберите плейлист по умолчанию:", target_menu(playlists, current is not None)


@router.message(Command("target"))
async def cmd_target(message: Message, ym: YandexMusic, store: Storage) -> None:
    text, markup = await target_prompt(ym, store, message.from_user.id)
    await message.answer(text, reply_markup=markup)


@router.callback_query(PlaylistCb.filter(F.action == "target"))
async def on_target(call: CallbackQuery, callback_data: PlaylistCb, ym: YandexMusic, store: Storage) -> None:
    playlist = await ym.get_playlist(callback_data.kind)
    if playlist is None:
        await call.answer("Плейлист не найден", show_alert=True)
        return
    store.set_upload_target(call.from_user.id, playlist.kind)
    await call.answer(f"📌 Плейлист по умолчанию для загрузки: «{playlist.title}». Изменить: /target", show_alert=True)


@router.callback_query(PlaylistCb.filter(F.action == "untarget"))
async def on_untarget(call: CallbackQuery, store: Storage) -> None:
    store.set_upload_target(call.from_user.id, None)
    await call.answer()
    await _edit(call.message, "Ок, плейлист по умолчанию убран: буду спрашивать, куда загружать файлы.", None)


@router.callback_query(PlaylistCb.filter(F.action == "delete"))
async def on_delete(call: CallbackQuery, callback_data: PlaylistCb, ym: YandexMusic) -> None:
    await call.answer()
    confirm = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🗑 Да, удалить",
                             callback_data=PlaylistCb(action="delete_ok", kind=callback_data.kind).pack()),
        InlineKeyboardButton(text="Отмена",
                             callback_data=ViewCb(src="pl", ref=f"{ym.uid}.{callback_data.kind}").pack()),
    ]])
    await _edit(call.message, "Точно удалить плейлист? Это нельзя отменить.", confirm)


@router.callback_query(PlaylistCb.filter(F.action == "delete_ok"))
async def on_delete_ok(call: CallbackQuery, callback_data: PlaylistCb, ym: YandexMusic, store: Storage) -> None:
    await ym.delete_playlist(callback_data.kind)
    if store.get_upload_target(call.from_user.id) == callback_data.kind:
        store.set_upload_target(call.from_user.id, None)
    await call.answer("Удалено")
    text, markup = await playlists_text(ym)
    await _edit(call.message, "🗑 Плейлист удалён.\n\n" + text, markup)


@router.message(Command("newplaylist"))
async def cmd_new_playlist(message: Message, command: CommandObject, state: FSMContext, ym: YandexMusic) -> None:
    if command.args:
        playlist = await ym.create_playlist(command.args.strip()[:100])
        await message.answer(f"✅ Плейлист «{html.escape(playlist.title)}» создан.")
        await show_source(message, ym, "pl", f"{ym.uid}.{playlist.kind}")
        return
    await state.set_state(NewPlaylist.name)
    await state.update_data(for_upload=False)
    await message.answer("Как назвать новый плейлист? (/cancel — отмена)")


@router.callback_query(PlaylistCb.filter(F.action == "new"))
async def on_new_playlist(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await state.set_state(NewPlaylist.name)
    await state.update_data(for_upload=False)
    await call.message.answer("Как назвать новый плейлист? (/cancel — отмена)")
