"""Скачивание треков: по одному и списком (альбом, плейлист, лайки)."""

from __future__ import annotations

import asyncio
import html
import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery

from bot.admin import LimitReached
from bot.callbacks import BulkCb, CancelBulkCb, TrackCb
from bot.errors import describe_error, log_failure, spawn
from bot.keyboards import cancel_bulk
from bot.sender import TrackSender, retry_telegram
from bot.sources import SourceNotFoundError, load_source, resolve
from bot.ym import YandexMusic, track_title

log = logging.getLogger(__name__)
router = Router(name="download")

# Активные массовые скачивания: user_id -> задача.
_bulk_tasks: dict[int, asyncio.Task] = {}


@router.callback_query(TrackCb.filter(F.action == "dl"))
async def on_download(
    call: CallbackQuery, callback_data: TrackCb, ym: YandexMusic, sender: TrackSender
) -> None:
    track = await ym.get_track(callback_data.track)
    if track is None:
        await call.answer("Трек не найден", show_alert=True)
        return
    await call.answer(f"⏳ Скачиваю «{track_title(track)}»…")
    try:
        await sender.send(call.message.chat.id, track, ym)
    except Exception as e:
        log_failure(log, "Не удалось отправить трек %s", track.id, exc=e)
        title = html.escape(track_title(track))
        await call.message.answer(f"😕 Не получилось скачать «{title}»: {html.escape(describe_error(e))}")


@router.callback_query(BulkCb.filter())
async def on_bulk(call: CallbackQuery, callback_data: BulkCb, bot: Bot, ym: YandexMusic, sender: TrackSender) -> None:
    user_id = call.from_user.id
    running = _bulk_tasks.get(user_id)
    if running and not running.done():
        await call.answer("Уже идёт скачивание — дождитесь или остановите его", show_alert=True)
        return
    await call.answer("Начинаю скачивание")
    start_bulk_download(bot, user_id, call.message.chat.id, callback_data.src, callback_data.ref, ym, sender)


def start_bulk_download(
    bot: Bot, user_id: int, chat_id: int, src: str, ref: str, ym: YandexMusic, sender: TrackSender
) -> bool:
    """Запускает отправку всего списка в чат. False — если у пользователя уже идёт скачивание."""
    running = _bulk_tasks.get(user_id)
    if running and not running.done():
        return False
    task = spawn(_bulk_download(bot, chat_id, src, ref, ym, sender), f"скачивание списка {src} для {user_id}")
    _bulk_tasks[user_id] = task
    task.add_done_callback(lambda t: _bulk_tasks.pop(user_id, None) if _bulk_tasks.get(user_id) is t else None)
    return True


@router.callback_query(CancelBulkCb.filter())
async def on_cancel_bulk(call: CallbackQuery) -> None:
    task = _bulk_tasks.get(call.from_user.id)
    if task and not task.done():
        task.cancel()
        await call.answer("Останавливаю…")
    else:
        await call.answer("Нечего останавливать")


async def _bulk_download(bot: Bot, chat_id: int, src: str, ref: str, ym: YandexMusic, sender: TrackSender) -> None:
    status = await bot.send_message(chat_id, "⏳ Собираю список треков…")

    async def update(text: str, with_cancel: bool = True) -> None:
        try:
            await retry_telegram(lambda: status.edit_text(text, reply_markup=cancel_bulk() if with_cancel else None))
        except TelegramBadRequest:
            pass

    sent, failed = 0, []
    total = 0
    try:
        source = await load_source(ym, src, ref)
        tracks = await resolve(ym, source.items)
        total = len(tracks)
        title = html.escape(source.title)
        for i, track in enumerate(tracks, 1):
            await update(f"⏳ {title}\nСкачиваю {i}/{total}: {html.escape(track_title(track))}")
            try:
                await sender.send(chat_id, track, ym)
                sent += 1
            except (asyncio.CancelledError, LimitReached):
                raise  # лимит на сегодня исчерпан — дальше качать бессмысленно
            except Exception as e:
                log_failure(log, "Трек %s не скачался", track.id, exc=e)
                failed.append(f"{track_title(track)} — {describe_error(e)}")
        text = f"✅ {title}\nГотово: {sent} из {total}."
    except asyncio.CancelledError:
        await update(f"⛔ Остановлено. Отправлено {sent} из {total}.", with_cancel=False)
        raise
    except SourceNotFoundError as e:
        text = f"😕 {html.escape(str(e))}"
    except LimitReached as e:
        text = f"⛔ {html.escape(str(e))}\nОтправлено {sent} из {total}."
    except Exception as e:
        log_failure(log, "Массовое скачивание прервалось", exc=e)
        text = f"⚠️ Скачивание прервалось: {html.escape(describe_error(e))}\nОтправлено {sent} из {total}."

    if failed:
        text += "\n\nНе удалось:\n" + "\n".join(f"• {html.escape(f)}" for f in failed[:20])
        if len(failed) > 20:
            text += f"\n…и ещё {len(failed) - 20}"
    await update(text[:4000], with_cancel=False)
