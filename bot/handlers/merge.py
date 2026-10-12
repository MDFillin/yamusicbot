"""Объединение и копирование плейлистов в чате: /merge (см. bot/merge.py)."""

from __future__ import annotations

import html

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.callbacks import MergeCb
from bot.keyboards import short
from bot.merge import LIKES, MAX_SOURCES, MergeResult, Source, merge_into, source_from_link
from bot.sources import SourceNotFoundError
from bot.states import MergeStates
from bot.ym import YandexMusic

router = Router(name="merge")

INTRO = """🔀 <b>Объединить или скопировать плейлисты</b>

В выбранный плейлист добавятся треки из других: ваших, «Мне нравится» или по ссылке — например, плейлиста \
друга, чтобы сделать общий. Повторы не добавляются, так что объединять можно снова — добавятся только новые.
Чтобы скопировать плейлист, выберите «В новый плейлист».

<b>Куда добавить треки?</b>"""


def _btn(text: str, cb: MergeCb) -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(text=short(text, 60), callback_data=cb.pack())]


async def _targets(ym: YandexMusic) -> InlineKeyboardMarkup:
    rows = [_btn(f"📃 {p.title} ({p.track_count or 0})", MergeCb(action="to", kind=p.kind))
            for p in await ym.get_my_playlists()]
    rows.append(_btn("➕ В новый плейлист (копия)", MergeCb(action="new")))
    rows.append(_btn("✖️ Отмена", MergeCb(action="cancel")))
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _card(ym: YandexMusic, data: dict) -> tuple[str, InlineKeyboardMarkup]:
    chosen = [Source(**s) for s in data.get("sources", [])]
    refs = {f"{s.src}:{s.ref}" for s in chosen}
    lines = [f"🔀 Добавить в «{html.escape(data['target_title'])}» треки из:"]
    lines += [f"• {html.escape(s.title)}" for s in chosen] or ["<i>пока ничего не выбрано</i>"]
    lines.append("\nОтметьте источники кнопками или пришлите ссылку на плейлист или альбом "
                 "(плейлист друга должен быть открытым).")
    rows = [_btn(("✅ " if "likes:" in refs else "◻️ ") + "❤️ Мне нравится", MergeCb(action="src", ref="likes"))]
    for p in await ym.get_my_playlists():
        if p.kind == data["target"]:
            continue
        mark = "✅ " if f"pl:{ym.uid}.{p.kind}" in refs else "◻️ "
        rows.append(_btn(f"{mark}{p.title} ({p.track_count or 0})", MergeCb(action="src", ref=f"pl:{p.kind}")))
    if chosen:
        rows.append(_btn(f"🔀 Объединить ({len(chosen)})", MergeCb(action="run")))
    rows.append(_btn("✖️ Отмена", MergeCb(action="cancel")))
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


async def _show_card(bot: Bot, chat_id: int, ym: YandexMusic, state: FSMContext, edit: Message | None = None) -> None:
    data = await state.get_data()
    text, markup = await _card(ym, data)
    if edit is not None:
        try:
            await edit.edit_text(text, reply_markup=markup)
            await state.update_data(card=edit.message_id)
            return
        except TelegramBadRequest as e:
            if "not modified" in str(e):
                return
    if data.get("card"):  # карточка ушла вверх — присылаем новую, старую убираем
        try:
            await bot.delete_message(chat_id, data["card"])
        except TelegramBadRequest:
            pass
    sent = await bot.send_message(chat_id, text, reply_markup=markup)
    await state.update_data(card=sent.message_id)


def result_text(r: MergeResult) -> str:
    if not r.added:
        text = f"👌 В «{html.escape(r.title)}» уже есть все эти треки — добавлять нечего."
    else:
        text = f"✅ В «{html.escape(r.title)}» добавлено треков: <b>{r.added}</b>."
    if r.added and r.already:
        text += f"\nУже были в плейлисте или повторялись: {r.already}."
    if r.skipped:
        text += f"\nПропущено чужих загруженных треков: {r.skipped} — Яндекс даёт добавлять их только владельцу."
    if r.over_limit:
        text += f"\n⚠️ Не поместились: {r.over_limit} — в плейлисте Яндекса не больше 10 000 треков."
    return text


@router.message(Command("merge"))
async def cmd_merge(message: Message, state: FSMContext, ym: YandexMusic) -> None:
    await state.clear()
    await message.answer(INTRO, reply_markup=await _targets(ym))


@router.callback_query(MergeCb.filter(F.action == "to"))
async def on_target(call: CallbackQuery, callback_data: MergeCb, state: FSMContext, bot: Bot, ym: YandexMusic) -> None:
    playlist = await ym.get_playlist(callback_data.kind)
    if playlist is None:
        await call.answer("Плейлист не найден — возможно, он удалён", show_alert=True)
        return
    await call.answer()
    await state.set_state(MergeStates.sources)
    await state.set_data({"target": playlist.kind, "target_title": playlist.title, "sources": []})
    await _show_card(bot, call.message.chat.id, ym, state, edit=call.message)


@router.callback_query(MergeCb.filter(F.action == "new"))
async def on_new(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await state.set_state(MergeStates.name)
    await call.message.edit_text("Как назвать новый плейлист? (/cancel — отмена)")


@router.message(MergeStates.name, F.text, ~F.text.startswith("/"))
async def on_name(message: Message, state: FSMContext, bot: Bot, ym: YandexMusic) -> None:
    playlist = await ym.create_playlist(message.text.strip()[:100])
    await state.set_state(MergeStates.sources)
    await state.set_data({"target": playlist.kind, "target_title": playlist.title, "sources": []})
    await _show_card(bot, message.chat.id, ym, state)


@router.callback_query(MergeCb.filter(F.action == "src"), MergeStates.sources)
async def on_source(call: CallbackQuery, callback_data: MergeCb, state: FSMContext, bot: Bot,
                    ym: YandexMusic) -> None:
    data = await state.get_data()
    if callback_data.ref == "likes":
        source = LIKES
    else:
        playlist = await ym.get_playlist(int(callback_data.ref.removeprefix("pl:")))
        if playlist is None:
            await call.answer("Плейлист не найден", show_alert=True)
            return
        source = Source("pl", f"{ym.uid}.{playlist.kind}", playlist.title)
    sources = data.get("sources", [])
    if any(s["src"] == source.src and s["ref"] == source.ref for s in sources):
        sources = [s for s in sources if not (s["src"] == source.src and s["ref"] == source.ref)]
    elif len(sources) >= MAX_SOURCES:
        await call.answer(f"Не больше {MAX_SOURCES} источников за раз", show_alert=True)
        return
    else:
        sources.append(source.json())
    await call.answer()
    await state.update_data(sources=sources)
    await _show_card(bot, call.message.chat.id, ym, state, edit=call.message)


@router.message(MergeStates.sources, F.text, ~F.text.startswith("/"))
async def on_link(message: Message, state: FSMContext, bot: Bot, ym: YandexMusic) -> None:
    try:
        source = await source_from_link(ym, message.text)
    except SourceNotFoundError as e:
        await message.reply(f"😕 {html.escape(str(e))}. /cancel — выйти из объединения.")
        return
    data = await state.get_data()
    sources = data.get("sources", [])
    if source.src == "pl" and source.ref == f"{ym.uid}.{data['target']}":
        await message.reply("Это тот самый плейлист, в который добавляем 🙂")
        return
    if not any(s["src"] == source.src and s["ref"] == source.ref for s in sources):
        if len(sources) >= MAX_SOURCES:
            await message.reply(f"Не больше {MAX_SOURCES} источников за раз.")
            return
        sources.append(source.json())
    await state.update_data(sources=sources)
    await _show_card(bot, message.chat.id, ym, state)


@router.callback_query(MergeCb.filter(F.action == "run"), MergeStates.sources)
async def on_run(call: CallbackQuery, state: FSMContext, ym: YandexMusic) -> None:
    data = await state.get_data()
    sources = [Source(**s) for s in data.get("sources", [])]
    if not sources:
        await call.answer("Сначала выберите, откуда брать треки", show_alert=True)
        return
    await call.answer()
    await state.clear()
    await call.message.edit_text(f"⏳ Собираю треки в «{html.escape(data['target_title'])}»…")
    try:
        result = await merge_into(ym, data["target"], sources)
    except SourceNotFoundError as e:
        await call.message.edit_text(f"😕 {html.escape(str(e))}")
        return
    await call.message.edit_text(result_text(result))


@router.callback_query(MergeCb.filter(F.action == "cancel"))
async def on_cancel(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await state.clear()
    await call.message.edit_text("Ок, ничего не объединяю.")


@router.callback_query(MergeCb.filter())
async def on_stale(call: CallbackQuery) -> None:
    await call.answer("Эта карточка устарела — начните заново: /merge", show_alert=True)
