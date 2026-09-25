"""Статистика прослушиваний в чате: /stats, переключение периодов и настройка итогов."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.callbacks import MenuCb, StatsCb, StatsSetCb
from bot.listening import MANUAL_SYNC_INTERVAL, Listening, msk_now, period_at
from bot.ym import YandexMusic

router = Router(name="stats")

INTRO = """📊 <b>Статистика прослушиваний</b>

Я буду следить за плеером Яндекс Музыки — в приложении, на сайте и на колонке — и считать каждое \
прослушивание, включая повторы, и сколько вы на самом деле слушали:
• любимые исполнители, треки, альбомы и жанры;
• новые открытия и серии дней с музыкой;
• сравнение с прошлой неделей и месяцем.

Итоги недели придут в воскресенье вечером, месяца — 1-го числа, дня — по желанию. Треки и исполнители \
в итогах — ссылки: нажали, и трек пришёл сюда.

<i>Статистика копится с момента включения. Прослушивание засчитывается, если трек слушали хотя бы \
половину (или 4 минуты). Если бот что-то пропустил, он доберёт это из истории Яндекса — но там без повторов. \
Выключить и удалить статистику можно в любой момент.</i>"""

PREF_LABELS = {"day": "Итоги дня (в 22:00)", "week": "Итоги недели (вс, 21:00)", "month": "Итоги месяца (1-го)"}


def _btn(text: str, key: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=StatsSetCb(key=key).pack())


async def _show(target: Message, text: str, markup: InlineKeyboardMarkup, edit: bool) -> None:
    if edit:
        try:
            await target.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
            return
        except TelegramBadRequest as e:
            if "not modified" in str(e):
                return
    await target.answer(text, reply_markup=markup, disable_web_page_preview=True)


async def show_stats(target: Message, user_id: int, listening: Listening, kind: str = "week", offset: int = 0,
                     edit: bool = False) -> None:
    if not listening.enabled(user_id):
        markup = InlineKeyboardMarkup(inline_keyboard=[[_btn("📊 Включить статистику", "on")]])
        await _show(target, INTRO, markup, edit)
        return
    offset = min(offset, 0)
    p = period_at(kind, msk_now().date(), offset)
    await _show(target, *await listening.report(user_id, p, offset), edit)


def settings_view(user_id: int, listening: Listening) -> tuple[str, InlineKeyboardMarkup]:
    prefs = listening.prefs(user_id)
    rows = [[_btn(("✅ " if prefs[k] else "⬜ ") + label, k)] for k, label in PREF_LABELS.items()]
    rows += [
        [_btn("🔄 Обновить историю сейчас", "sync")],
        [_btn("🚫 Выключить статистику", "off"), _btn("🗑 Удалить историю", "delete")],
        [InlineKeyboardButton(text="‹ К статистике", callback_data=StatsCb(kind="week").pack())],
    ]
    since = listening.store.listening_since(user_id)
    note = f"\nСобираю с {since[8:10]}.{since[5:7]}.{since[:4]}." if since else ""
    return f"⚙️ <b>Итоги в чат</b>\nКакие итоги присылать сообщением.{note}", InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(Command("stats"))
async def cmd_stats(message: Message, listening: Listening) -> None:
    await show_stats(message, message.from_user.id, listening)


@router.callback_query(MenuCb.filter(F.action == "stats"))
async def menu_stats(call: CallbackQuery, listening: Listening) -> None:
    await call.answer()
    await show_stats(call.message, call.from_user.id, listening)


@router.callback_query(StatsCb.filter())
async def on_period(call: CallbackQuery, callback_data: StatsCb, listening: Listening) -> None:
    if callback_data.kind not in ("day", "week", "month", "year") or callback_data.offset < -520:
        await call.answer()
        return
    await call.answer()
    await show_stats(call.message, call.from_user.id, listening, callback_data.kind, callback_data.offset, edit=True)


@router.callback_query(StatsSetCb.filter())
async def on_setting(call: CallbackQuery, callback_data: StatsSetCb, listening: Listening, ym: YandexMusic) -> None:
    user_id, key = call.from_user.id, callback_data.key
    if key == "on":
        listening.enable(user_id)
        await call.answer("📊 Статистика включена")
        if listening.can_sync_now(user_id):  # повторное включение не дёргает Яндекс чаще раза в минуту
            await _show(call.message, "⏳ Забираю историю из Яндекс Музыки…",
                        InlineKeyboardMarkup(inline_keyboard=[]), edit=True)
            if await listening.sync(user_id, ym) is None:
                await call.message.answer("😕 Не получилось забрать историю сейчас — попробую снова через час.")
        await show_stats(call.message, user_id, listening, edit=True)
        return
    if key in ("off", "delete_ok"):
        deleted = listening.disable(user_id, delete=key == "delete_ok")
        await call.answer("Статистика выключена" + (f", удалено записей: {deleted}" if deleted else ""))
        await show_stats(call.message, user_id, listening, edit=True)
        return
    if key == "delete":
        await call.answer()
        markup = InlineKeyboardMarkup(inline_keyboard=[[_btn("🗑 Да, удалить", "delete_ok"), _btn("Отмена", "menu")]])
        await _show(call.message, "Удалить всю собранную историю прослушиваний и выключить статистику? "
                                  "Вернуть её не получится: Яндекс хранит историю недолго.", markup, edit=True)
        return
    if not listening.enabled(user_id):
        await call.answer("Статистика выключена", show_alert=True)
        return
    if key == "sync":
        if not listening.can_sync_now(user_id):
            await call.answer(f"Обновлять можно раз в {MANUAL_SYNC_INTERVAL} секунд", show_alert=True)
            return
        added = await listening.sync(user_id, ym)
        await call.answer("Не получилось, попробуйте позже" if added is None else f"Новых прослушиваний: {added}")
    elif key in PREF_LABELS:
        value = not listening.prefs(user_id)[key]
        listening.set_pref(user_id, key, value)
        await call.answer(("Будут приходить: " if value else "Больше не присылаю: ") + PREF_LABELS[key].lower())
    else:
        await call.answer()
    text, markup = settings_view(user_id, listening)
    await _show(call.message, text, markup, edit=True)
