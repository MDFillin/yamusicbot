"""Админ-панель в чате: /admin, /user, /ban, /unban, /broadcast. Работает только для ADMIN_IDS из .env.

Для всех остальных этих команд просто нет: фильтр IsAdmin пропускает их сообщения дальше, и бот отвечает
как на любую непонятную команду.
"""

from __future__ import annotations

import html
import time
from typing import Any

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, Filter, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    ChatMemberUpdated,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    TelegramObject,
    WebAppInfo,
)

from bot.admin import AUDIENCES, Admin, display_name
from bot.callbacks import AdminCb
from bot.handlers.common import PUBLIC
from bot.states import AdminStates

router = Router(name="admin")


class IsAdmin(Filter):
    async def __call__(self, event: TelegramObject, admin: Admin) -> bool:
        user = getattr(event, "from_user", None)
        return user is not None and admin.is_admin(user.id)


router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


def _btn(text: str, action: str, user: int = 0) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=AdminCb(action=action, user=user).pack())


def _ago(ts: int | None) -> str:
    if not ts:
        return "—"
    delta = int(time.time() - ts)
    if delta < 120:
        return "только что"
    if delta < 3600:
        return f"{delta // 60} мин назад"
    if delta < 86400:
        return f"{delta // 3600} ч назад"
    return time.strftime("%d.%m.%Y", time.localtime(ts))


def panel(admin: Admin) -> tuple[str, InlineKeyboardMarkup]:
    st = admin.stats(days=1)
    u, s = st["users"], admin.settings
    limits = []
    if s.download_limit:
        limits.append(f"скачиваний {s.download_limit}/сутки")
    if s.upload_limit:
        limits.append(f"загрузок {s.upload_limit}/сутки")
    text = (
        "🛡 <b>Админ-панель</b>\n\n"
        f"👥 Пользователей: <b>{u['total']}</b> (с Яндексом {u['connected']})\n"
        f"🆕 Новых: сегодня {u['new_today']}, за неделю {u['new_week']}\n"
        f"🟢 Активны: сегодня {u['active_today']}, за неделю {u['active_week']}, за месяц {u['active_month']}\n"
        f"⬇️ Скачано: сегодня {st['downloads']['today']}, всего {st['downloads']['total']}\n"
        f"⬆️ Загружено: сегодня {st['uploads']['today']}, всего {st['uploads']['total']}\n"
        f"⛔ Заблокированы: {u['banned']} · бота заблокировали: {u['blocked']}\n"
        f"⚠️ Ошибок за сутки: {st['errors_24h']}\n\n"
        f"🛠 Техработы: <b>{'включены' if s.maintenance else 'выключены'}</b>\n"
        f"🚪 Регистрация: <b>{'закрыта' if s.closed_since else 'открыта'}</b>\n"
        f"📏 Лимиты: {', '.join(limits) if limits else 'нет'}"
    )
    if admin.broadcaster.running:
        text += "\n\n" + admin.broadcaster.summary()
    rows = []
    if admin.config.webapp_url:
        rows.append([InlineKeyboardButton(text="📊 Открыть полную панель",
                                          web_app=WebAppInfo(url=f"{admin.config.webapp_url}/?admin=1"))])
    rows += [
        [_btn("🛠 Выключить техработы" if s.maintenance else "🛠 Включить техработы", "maintenance"),
         _btn("🚪 Открыть регистрацию" if s.closed_since else "🚪 Закрыть регистрацию", "closed")],
        [_btn("📣 Рассылка", "broadcast"), _btn("💾 Бэкап базы", "backup")],
        [_btn("⚠️ Ошибки", "errors"), _btn("📜 Журнал", "audit")],
        [_btn("🔄 Обновить", "refresh")],
    ]
    if admin.broadcaster.running:
        rows.insert(-1, [_btn("⏹ Остановить рассылку", "bc_stop")])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def user_card(admin: Admin, user_id: int) -> tuple[str, InlineKeyboardMarkup] | None:
    user = admin.store.get_user(user_id)
    if user is None:
        return None
    lines = [
        f"👤 <b>{html.escape(display_name(user))}</b>",
        f"ID: <code>{user['id']}</code>",
        f"Яндекс: {html.escape(user['yandex_login'] or '—') if user['connected'] else 'не подключён'}",
        f"Впервые: {_ago(user['first_seen'])} · был: {_ago(user['last_seen'])}",
        f"Скачал: {user['downloads']} · загрузил: {user['uploads']}",
    ]
    if user["banned"]:
        lines.append("⛔ <b>Заблокирован</b>" + (f": {html.escape(user['ban_reason'])}" if user["ban_reason"] else ""))
    if user["blocked_bot"]:
        lines.append("🚫 Заблокировал бота")
    if admin.is_admin(user_id):
        lines.append("🛡 Админ")
    rows = []
    if not admin.is_admin(user_id):
        rows.append([_btn("✅ Разблокировать", "unban", user_id) if user["banned"]
                     else _btn("⛔ Заблокировать", "ban", user_id)])
    row = [_btn("♻️ Сбросить лимиты", "reset", user_id)]
    if user["connected"]:
        row.insert(0, _btn("🔌 Отключить Яндекс", "disconnect", user_id))
    rows.append(row)
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


def _target(admin: Admin, arg: str | None) -> int | None:
    """ID из аргумента команды: число или @username."""
    arg = (arg or "").split(maxsplit=1)[0] if arg else ""
    if arg.lstrip("-").isdigit():
        return int(arg)
    if arg:
        found = admin.store.list_users(arg.lstrip("@"), limit=2)
        if len(found) == 1:
            return found[0]["id"]
    return None


async def _edit(message: Message, text: str, markup: InlineKeyboardMarkup | None) -> None:
    try:
        await message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest:
        pass  # не изменилось


@router.my_chat_member()
async def on_bot_blocked(update: ChatMemberUpdated, admin: Admin) -> None:
    """Пользователь заблокировал бота — отмечаем сразу (для статистики и чтобы не слать ему рассылку)."""
    if update.chat.type == "private" and update.new_chat_member.status == "kicked":
        admin.mark_blocked(update.from_user.id)


# ---------- главное меню ----------

@router.message(Command("admin"), flags=PUBLIC)
async def cmd_admin(message: Message, state: FSMContext, admin: Admin) -> None:
    await state.clear()
    text, markup = panel(admin)
    await message.answer(text, reply_markup=markup)


@router.callback_query(AdminCb.filter(F.action.in_({"refresh", "maintenance", "closed", "bc_stop"})), flags=PUBLIC)
async def on_panel_action(call: CallbackQuery, callback_data: AdminCb, admin: Admin) -> None:
    actor = call.from_user.id
    if callback_data.action == "maintenance":
        admin.update_settings(actor, {"maintenance": not admin.settings.maintenance})
        await call.answer("🛠 Техработы " + ("включены" if admin.settings.maintenance else "выключены"))
    elif callback_data.action == "closed":
        admin.update_settings(actor, {"closed": admin.settings.closed_since is None})
        await call.answer("🚪 Регистрация " + ("закрыта" if admin.settings.closed_since else "открыта"))
    elif callback_data.action == "bc_stop":
        await call.answer("Останавливаю рассылку…" if admin.cancel_broadcast(actor) else "Рассылка не идёт")
    else:
        await call.answer("Обновлено")
    text, markup = panel(admin)
    await _edit(call.message, text, markup)


@router.callback_query(AdminCb.filter(F.action == "backup"), flags=PUBLIC)
async def on_backup(call: CallbackQuery, admin: Admin) -> None:
    await call.answer("💾 Готовлю копию…")
    size = await admin.backup(call.from_user.id)
    await call.message.answer(f"💾 Копия базы отправлена ({max(1, size // 1024)} КБ).")


@router.callback_query(AdminCb.filter(F.action == "errors"), flags=PUBLIC)
async def on_errors(call: CallbackQuery, admin: Admin) -> None:
    await call.answer()
    items = admin.errors.recent(limit=5)
    if not items:
        await call.message.answer("✅ Ошибок и предупреждений нет.")
        return
    parts = ["⚠️ <b>Последние ошибки</b> (полный список — в панели)"]
    for e in items:
        stamp = time.strftime("%d.%m %H:%M", time.localtime(e["at"]))
        parts.append(f"<b>{stamp}</b> {e['level']} {html.escape(e['logger'])}\n"
                     f"<pre>{html.escape(e['message'][-600:])}</pre>")
    await call.message.answer("\n\n".join(parts)[:4000])


@router.callback_query(AdminCb.filter(F.action == "audit"), flags=PUBLIC)
async def on_audit(call: CallbackQuery, admin: Admin) -> None:
    await call.answer()
    entries = admin.audit_log(15)
    if not entries:
        await call.message.answer("📜 Журнал пуст.")
        return
    lines = ["📜 <b>Журнал действий</b>"]
    for e in entries:
        stamp = time.strftime("%d.%m %H:%M", time.localtime(e["at"]))
        who = html.escape(e["actor_name"] or "?")
        what = html.escape(e["label"])
        target = f" {html.escape(e['target_name'])}" if e["target"] and e["target"] != e["actor"] else ""
        details = f" — {html.escape(e['details'])}" if e["details"] else ""
        lines.append(f"<b>{stamp}</b> {who}: {what}{target}{details}")
    await call.message.answer("\n".join(lines)[:4000])


# ---------- пользователи ----------

@router.message(Command("user"), flags=PUBLIC)
async def cmd_user(message: Message, command: CommandObject, admin: Admin) -> None:
    user_id = _target(admin, command.args)
    card = user_card(admin, user_id) if user_id is not None else None
    if card is None:
        await message.answer("Укажите ID или @username: <code>/user 123456789</code>")
        return
    await message.answer(card[0], reply_markup=card[1])


@router.message(Command("ban"), flags=PUBLIC)
async def cmd_ban(message: Message, command: CommandObject, admin: Admin) -> None:
    user_id = _target(admin, command.args)
    if user_id is None:
        await message.answer("Формат: <code>/ban ID причина</code> (причина — по желанию)")
        return
    parts = (command.args or "").split(maxsplit=1)
    try:
        admin.ban(message.from_user.id, user_id, parts[1] if len(parts) > 1 else None)
    except ValueError as e:
        await message.answer(f"😕 {html.escape(str(e))}")
        return
    await message.answer(f"⛔ Пользователь <code>{user_id}</code> заблокирован.")


@router.message(Command("unban"), flags=PUBLIC)
async def cmd_unban(message: Message, command: CommandObject, admin: Admin) -> None:
    user_id = _target(admin, command.args)
    if user_id is None:
        await message.answer("Формат: <code>/unban ID</code>")
        return
    admin.unban(message.from_user.id, user_id)
    await message.answer(f"✅ Пользователь <code>{user_id}</code> разблокирован.")


@router.callback_query(AdminCb.filter(F.action.in_({"ban", "unban", "disconnect", "reset"})), flags=PUBLIC)
async def on_user_action(call: CallbackQuery, callback_data: AdminCb, admin: Admin) -> None:
    actor, user_id = call.from_user.id, callback_data.user
    try:
        if callback_data.action == "ban":
            admin.ban(actor, user_id)
            note = "⛔ Заблокирован"
        elif callback_data.action == "unban":
            admin.unban(actor, user_id)
            note = "✅ Разблокирован"
        elif callback_data.action == "disconnect":
            await admin.disconnect(actor, user_id)
            note = "🔌 Яндекс отключён"
        else:
            admin.reset_limits(actor, user_id)
            note = "♻️ Лимиты на сегодня сброшены"
    except ValueError as e:
        await call.answer(str(e), show_alert=True)
        return
    await call.answer(note)
    if card := user_card(admin, user_id):
        await _edit(call.message, card[0], card[1])


# ---------- рассылка ----------

def _broadcast_markup(admin: Admin) -> InlineKeyboardMarkup:
    everyone = len(admin.store.user_ids("all"))
    connected = len(admin.store.user_ids("connected"))
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(f"📣 Всем ({everyone})", "bc_all")],
        [_btn(f"🔑 С подключённым Яндексом ({connected})", "bc_connected")],
        [_btn("👤 Сначала себе — проверить", "bc_test"), _btn("✖️ Отмена", "bc_cancel")],
    ])


@router.message(Command("broadcast"), flags=PUBLIC)
async def cmd_broadcast(message: Message, state: FSMContext, admin: Admin) -> None:
    await _ask_broadcast(message, state, admin)


@router.callback_query(AdminCb.filter(F.action == "broadcast"), flags=PUBLIC)
async def on_broadcast(call: CallbackQuery, state: FSMContext, admin: Admin) -> None:
    await call.answer()
    await _ask_broadcast(call.message, state, admin)


async def _ask_broadcast(message: Message, state: FSMContext, admin: Admin) -> None:
    if admin.broadcaster.running:
        await message.answer(admin.broadcaster.summary(), reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[_btn("⏹ Остановить рассылку", "bc_stop")]]))
        return
    await state.set_state(AdminStates.broadcast)
    await message.answer("📣 Пришлите текст рассылки — можно с форматированием (жирный, ссылки и т. п.).\n"
                         "Перед отправкой покажу, как он будет выглядеть. /cancel — отмена.")


@router.message(StateFilter(AdminStates.broadcast), F.text, ~F.text.startswith("/"), flags=PUBLIC)
async def on_broadcast_text(message: Message, state: FSMContext, admin: Admin) -> None:
    text = message.html_text
    if len(text) > 4000:
        await message.answer("😕 Слишком длинно: Telegram принимает до 4096 символов. Пришлите покороче.")
        return
    await state.update_data(text=text)
    await message.answer(text, disable_web_page_preview=True)
    await message.answer("☝️ Так увидят сообщение пользователи. Кому отправить?", reply_markup=_broadcast_markup(admin))


@router.callback_query(AdminCb.filter(F.action.startswith("bc_")), flags=PUBLIC)
async def on_broadcast_choice(call: CallbackQuery, callback_data: AdminCb, state: FSMContext, admin: Admin,
                              bot: Bot) -> None:
    data: dict[str, Any] = await state.get_data()
    text = data.get("text")
    if callback_data.action == "bc_cancel" or not text:
        await state.clear()
        await call.answer("Отменено" if text else "Текст рассылки устарел — начните заново: /broadcast")
        await _edit(call.message, "Рассылка отменена.", None)
        return
    if callback_data.action == "bc_test":
        await bot.send_message(call.from_user.id, text, disable_web_page_preview=True)
        await call.answer("Отправил вам")
        return
    audience = "connected" if callback_data.action == "bc_connected" else "all"
    try:
        status = admin.start_broadcast(call.from_user.id, text, audience)
    except (RuntimeError, ValueError) as e:
        await call.answer(str(e), show_alert=True)
        return
    await state.clear()
    await call.answer("📣 Рассылка запущена")
    await _edit(call.message, f"📣 Рассылка запущена: {status['total']} получателей ({AUDIENCES[audience]}).\n"
                              "Когда закончится, пришлю итог.",
                InlineKeyboardMarkup(inline_keyboard=[[_btn("⏹ Остановить", "bc_stop")]]))
