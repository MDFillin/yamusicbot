"""Общие плейлисты в чате: /shared, приглашение t.me/<бот>?start=join_<код> (см. bot/shared.py)."""

from __future__ import annotations

import contextlib
import html
import time

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.callbacks import SharedCb
from bot.keyboards import plural, short
from bot.shared import SharedError, SharedPlaylists
from bot.states import SharedStates
from bot.ym import YandexMusic

router = Router(name="shared")
join_router = Router(name="shared_join")  # раньше общего /start: ссылка-приглашение start=join_<код>
PUBLIC = {"public": True}

HOW = ("Добавляйте и удаляйте треки в этом плейлисте прямо в приложении Яндекс Музыки — у остальных изменения "
       "появятся в течение нескольких минут, и наоборот.")
INTRO = f"""🤝 <b>Общие плейлисты</b>

Один плейлист на нескольких человек. У каждого он лежит в своём аккаунте Яндекса, а бот держит их одинаковыми. \
{HOW}
Друзья присоединяются по ссылке-приглашению (им тоже нужен этот бот)."""


def _btn(text: str, cb: SharedCb) -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(text=short(text, 60), callback_data=cb.pack())]


def _ago(ts: int | None) -> str:
    if not ts:
        return "ещё не синхронизирован"
    minutes = int((time.time() - ts) // 60)
    if minutes < 1:
        return "синхронизирован только что"
    if minutes < 120:
        return f"синхронизирован {minutes} мин назад"
    return f"синхронизирован {minutes // 60} ч назад"


async def invite_link(bot: Bot, code: str) -> str:
    return f"https://t.me/{(await bot.me()).username}?start=join_{code}"


async def _show(message: Message, text: str, markup: InlineKeyboardMarkup, edit: bool) -> None:
    if edit:
        try:
            await message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
            return
        except TelegramBadRequest as e:
            if "not modified" in str(e):
                return
    await message.answer(text, reply_markup=markup, disable_web_page_preview=True)


async def show_list(message: Message, user_id: int, shared: SharedPlaylists, edit: bool = False) -> None:
    rows = []
    for s in shared.store.shared_for_user(user_id):
        n = len(shared.store.shared_members(s["id"]))
        rows.append(_btn(f"🤝 {s['title']} · {n} {plural(n, 'участник', 'участника', 'участников')}",
                         SharedCb(action="open", sid=s["id"])))
    rows.append(_btn("➕ Создать общий плейлист", SharedCb(action="create")))
    await _show(message, INTRO, InlineKeyboardMarkup(inline_keyboard=rows), edit)


async def show_card(message: Message, user_id: int, sid: int, shared: SharedPlaylists, edit: bool = True) -> None:
    info = shared.describe(sid, user_id)
    if info is None:
        await _show(message, "😕 Этого общего плейлиста больше нет или вы из него вышли.",
                    InlineKeyboardMarkup(inline_keyboard=[_btn("« Общие плейлисты", SharedCb(action="list"))]), edit)
        return
    members = ", ".join(html.escape(m["name"]) + (" (вы)" if m["me"] else "") + (" ⚠️" if m["error"] else "")
                        for m in info["members"])
    lines = [f"🤝 <b>{html.escape(info['title'])}</b>", f"Участники: {members}",
             f"Треков: {info['tracks']} · {_ago(info['synced_at'])}", "", HOW]
    if info["error"]:
        lines.append(f"\n⚠️ У вас не получилось: {html.escape(info['error'])}")
    if len(info["members"]) < 2:
        lines.append("\nПозовите друга — нажмите «Ссылка-приглашение» и отправьте ему ссылку.")
    rows = [_btn("🔄 Синхронизировать сейчас", SharedCb(action="sync", sid=sid)),
            _btn("🔗 Ссылка-приглашение", SharedCb(action="invite", sid=sid)),
            _btn("🚪 Выйти", SharedCb(action="leave", sid=sid)),
            _btn("« Общие плейлисты", SharedCb(action="list"))]
    await _show(message, "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows), edit)


@router.message(Command("shared"))
async def cmd_shared(message: Message, state: FSMContext, shared: SharedPlaylists) -> None:
    await state.clear()
    await show_list(message, message.from_user.id, shared)


@router.callback_query(SharedCb.filter(F.action == "list"))
async def on_list(call: CallbackQuery, shared: SharedPlaylists) -> None:
    await call.answer()
    await show_list(call.message, call.from_user.id, shared, edit=True)


@router.callback_query(SharedCb.filter(F.action == "open"))
async def on_open(call: CallbackQuery, callback_data: SharedCb, shared: SharedPlaylists, bot: Bot) -> None:
    await call.answer()
    await show_card(call.message, call.from_user.id, callback_data.sid, shared)


# ---------- создать ----------

@router.callback_query(SharedCb.filter(F.action == "create"))
async def on_create(call: CallbackQuery, ym: YandexMusic) -> None:
    await call.answer()
    rows = [_btn("➕ Новый плейлист", SharedCb(action="create_new"))]
    rows += [_btn(f"📃 {p.title} ({p.track_count or 0})", SharedCb(action="create_from", kind=p.kind))
             for p in await ym.get_my_playlists()]
    rows.append(_btn("« Назад", SharedCb(action="list")))
    await _show(call.message, "Какой плейлист сделать общим? Можно новый или один из ваших — "
                "тогда его треки сразу окажутся у всех участников.", InlineKeyboardMarkup(inline_keyboard=rows), True)


@router.callback_query(SharedCb.filter(F.action == "create_new"))
async def on_create_new(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await state.set_state(SharedStates.name)
    await call.message.edit_text("Как назвать общий плейлист? (/cancel — отмена)")


@router.message(SharedStates.name, F.text, ~F.text.startswith("/"))
async def on_name(message: Message, state: FSMContext, bot: Bot, ym: YandexMusic, shared: SharedPlaylists) -> None:
    await state.clear()
    playlist = await ym.create_playlist(message.text.strip()[:100])
    await _created(message, message.from_user.id, playlist.title, playlist.kind, shared, bot)


@router.callback_query(SharedCb.filter(F.action == "create_from"))
async def on_create_from(call: CallbackQuery, callback_data: SharedCb, bot: Bot, ym: YandexMusic,
                         shared: SharedPlaylists) -> None:
    playlist = await ym.get_playlist(callback_data.kind)
    if playlist is None:
        await call.answer("Плейлист не найден", show_alert=True)
        return
    await call.answer()
    await _created(call.message, call.from_user.id, playlist.title, playlist.kind, shared, bot)


async def _created(message: Message, user_id: int, title: str, kind: int, shared: SharedPlaylists, bot: Bot) -> None:
    try:
        sid = shared.create(user_id, title, kind)
    except SharedError as e:
        await message.answer(f"😕 {html.escape(str(e))}")
        return
    shared.sync_soon(sid)
    link = await invite_link(bot, shared.store.get_shared(sid)["code"])
    await message.answer(f"✅ Общий плейлист «{html.escape(title)}» готов.\n\nОтправьте другу эту ссылку — "
                         f"по ней он присоединится:\n{link}\n\n{HOW}", disable_web_page_preview=True,
                         reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                             _btn("🤝 Открыть", SharedCb(action="open", sid=sid))]))


# ---------- управление ----------

@router.callback_query(SharedCb.filter(F.action == "sync"))
async def on_sync(call: CallbackQuery, callback_data: SharedCb, bot: Bot, shared: SharedPlaylists) -> None:
    sid = callback_data.sid
    if shared.describe(sid, call.from_user.id) is None:
        await call.answer("Вы не в этом общем плейлисте", show_alert=True)
        return
    if not shared.can_sync_now(sid):
        await call.answer("Только что синхронизировал — подождите немного")
        return
    await call.answer("Синхронизирую…")
    report = await shared.sync(sid)
    if report is not None and (report.added or report.removed):
        await call.message.answer(f"🔄 Готово: добавлено {report.added}, удалено {report.removed}.")
    await show_card(call.message, call.from_user.id, sid, shared)


@router.callback_query(SharedCb.filter(F.action == "invite"))
async def on_invite(call: CallbackQuery, callback_data: SharedCb, bot: Bot, shared: SharedPlaylists) -> None:
    info = shared.describe(callback_data.sid, call.from_user.id)
    if info is None:
        await call.answer("Вы не в этом общем плейлисте", show_alert=True)
        return
    await call.answer()
    await call.message.answer(f"🔗 Приглашение в общий плейлист «{html.escape(info['title'])}» — перешлите его другу:\n"
                              f"{await invite_link(bot, info['code'])}", disable_web_page_preview=True)


@router.callback_query(SharedCb.filter(F.action == "leave"))
async def on_leave(call: CallbackQuery, callback_data: SharedCb, shared: SharedPlaylists) -> None:
    info = shared.describe(callback_data.sid, call.from_user.id)
    if info is None:
        await call.answer("Вы уже не в этом общем плейлисте", show_alert=True)
        return
    await call.answer()
    await _show(call.message, f"Выйти из общего плейлиста «{html.escape(info['title'])}»? Ваш плейлист останется "
                "как есть, просто перестанет обновляться. У остальных ничего не удалится.",
                InlineKeyboardMarkup(inline_keyboard=[_btn("🚪 Выйти", SharedCb(action="leave_ok", sid=info["id"])),
                                                      _btn("« Назад", SharedCb(action="open", sid=info["id"]))]), True)


@router.callback_query(SharedCb.filter(F.action == "leave_ok"))
async def on_leave_ok(call: CallbackQuery, callback_data: SharedCb, shared: SharedPlaylists) -> None:
    await call.answer()
    shared.leave(callback_data.sid, call.from_user.id)
    await show_list(call.message, call.from_user.id, shared, edit=True)


# ---------- присоединиться по приглашению ----------

@join_router.message(CommandStart(deep_link=True, magic=F.args.regexp(r"^join_[\w-]{6,32}$")), flags=PUBLIC)
async def start_join(message: Message, command: CommandObject, state: FSMContext, shared: SharedPlaylists,
                     ym: YandexMusic | None) -> None:
    await state.clear()
    code = command.args.removeprefix("join_")
    info = shared.store.shared_by_code(code)
    if info is None:
        await message.answer("😕 Приглашение недействительно: общий плейлист удалён.")
        return
    if ym is None:
        await message.answer("🤝 Чтобы присоединиться к общему плейлисту, сначала подключите Яндекс Музыку: /login\n"
                             "Потом снова откройте ссылку-приглашение.")
        return
    members = shared.store.shared_members(info["id"])
    if any(m["user_id"] == message.from_user.id for m in members):
        await show_card(message, message.from_user.id, info["id"], shared, edit=False)
        return
    await state.update_data(join_code=code)
    names = ", ".join(html.escape(shared.name(m["user_id"])) for m in members)
    await message.answer(
        f"🤝 <b>Общий плейлист «{html.escape(info['title'])}»</b>\nУчастники: {names}\n\n"
        f"У вас в Яндекс Музыке будет такой же плейлист. {HOW}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            _btn(f"✅ Создать у меня «{info['title']}»", SharedCb(action="join_new", sid=info["id"])),
            _btn("📃 Использовать мой плейлист…", SharedCb(action="join_pick", sid=info["id"])),
            _btn("✖️ Не надо", SharedCb(action="join_no")),
        ]))


async def _invited(call: CallbackQuery, state: FSMContext, shared: SharedPlaylists, sid: int) -> str | None:
    """Код приглашения из /start join_…, если он от этого общего плейлиста (номер в кнопке не подделать)."""
    code = (await state.get_data()).get("join_code")
    info = shared.store.shared_by_code(code) if code else None
    if info is None or info["id"] != sid:
        await call.answer("Откройте ссылку-приглашение ещё раз", show_alert=True)
        return None
    return code


@router.callback_query(SharedCb.filter(F.action == "join_pick"))
async def on_join_pick(call: CallbackQuery, callback_data: SharedCb, state: FSMContext, ym: YandexMusic,
                       shared: SharedPlaylists) -> None:
    if await _invited(call, state, shared, callback_data.sid) is None:
        return
    await call.answer()
    rows = [_btn(f"📃 {p.title} ({p.track_count or 0})", SharedCb(action="join_to", sid=callback_data.sid, kind=p.kind))
            for p in await ym.get_my_playlists()]
    rows.append(_btn("✖️ Не надо", SharedCb(action="join_no")))
    await _show(call.message, "Какой из ваших плейлистов сделать общим? Его треки окажутся у всех участников, "
                "а их треки — в нём.", InlineKeyboardMarkup(inline_keyboard=rows), True)


@router.callback_query(SharedCb.filter(F.action.in_({"join_new", "join_to"})))
async def on_join(call: CallbackQuery, callback_data: SharedCb, state: FSMContext, bot: Bot, ym: YandexMusic,
                  shared: SharedPlaylists) -> None:
    code = await _invited(call, state, shared, callback_data.sid)
    if code is None:
        return
    if callback_data.action == "join_to":
        playlist = await ym.get_playlist(callback_data.kind)
        if playlist is None:
            await call.answer("Плейлист не найден", show_alert=True)
            return
    else:
        playlist = await ym.create_playlist(shared.store.shared_by_code(code)["title"])
    try:
        info = shared.join(code, call.from_user.id, playlist.kind)
    except SharedError as e:
        await call.answer(str(e), show_alert=True)
        return
    await call.answer()
    await state.clear()
    shared.sync_soon(info["id"])
    await call.message.edit_text(f"✅ Вы в общем плейлисте «{html.escape(info['title'])}» — плейлист «"
                                 f"{html.escape(playlist.title)}» в вашей Яндекс Музыке. Через минуту в нём будут "
                                 f"треки остальных.\n\n{HOW}")
    for m in shared.store.shared_members(info["id"]):
        if m["user_id"] != call.from_user.id:
            with contextlib.suppress(TelegramAPIError):  # уведомление необязательное
                text = (f"🤝 {html.escape(shared.name(call.from_user.id))} присоединился к общему плейлисту "
                        f"«{html.escape(info['title'])}».")
                await bot.send_message(m["user_id"], text)


@router.callback_query(SharedCb.filter(F.action == "join_no"))
async def on_join_no(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await state.clear()
    await call.message.edit_text("Ок, не присоединяюсь.")
