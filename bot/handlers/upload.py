"""Загрузка своих аудиофайлов в Яндекс Музыку."""

from __future__ import annotations

import asyncio
import html
import io
import logging
from collections import defaultdict
from dataclasses import dataclass

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.audio import (
    ConversionError,
    is_audio_filename,
    parse_caption,
    prepare_for_upload,
    safe_filename,
)
from bot.callbacks import UploadCb
from bot.config import Config
from bot.keyboards import upload_targets
from bot.sender import retry_telegram
from bot.states import NewPlaylist
from bot.storage import Storage
from bot.ym import UploadError, YandexMusic

log = logging.getLogger(__name__)
router = Router(name="upload")


@dataclass(frozen=True)
class PendingFile:
    file_id: str
    file_name: str
    file_size: int | None
    caption: str | None
    performer: str | None = None
    title: str | None = None


class UploadQueue:
    """Файлы, для которых пользователь ещё не выбрал плейлист, и сообщение с выбором."""

    def __init__(self) -> None:
        self.pending: dict[int, list[PendingFile]] = defaultdict(list)
        self.prompts: dict[int, Message] = {}
        self.queue_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.upload_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    def take(self, user_id: int) -> list[PendingFile]:
        self.prompts.pop(user_id, None)
        return self.pending.pop(user_id, [])


def pending_from_message(message: Message) -> PendingFile | None:
    if message.audio:
        a = message.audio
        name = a.file_name or safe_filename(" - ".join(x for x in (a.performer, a.title) if x) or "track") + ".mp3"
        return PendingFile(a.file_id, name, a.file_size, message.caption, a.performer, a.title)
    doc = message.document
    if doc and ((doc.mime_type or "").startswith("audio/") or is_audio_filename(doc.file_name)):
        return PendingFile(doc.file_id, doc.file_name or "track.mp3", doc.file_size, message.caption)
    return None


def _is_audio_message(message: Message) -> bool:
    return pending_from_message(message) is not None


async def prepare_file(pf: PendingFile, data: bytes) -> tuple[str, bytes, list[str]]:
    """Готовит присланный в чат файл: подпись «Исполнитель - Название» важнее тегов из Telegram."""
    artist, title = parse_caption(pf.caption) or (None, None)
    return await prepare_for_upload(
        pf.file_name, data, artist=artist, title=title,
        fallback_artist=pf.performer, fallback_title=pf.title,
    )


async def download_from_telegram(bot: Bot, file_id: str) -> bytes:
    buf = io.BytesIO()
    await bot.download(file_id, destination=buf, timeout=600)
    return buf.getvalue()


async def upload_files(
    bot: Bot, chat_id: int, user_id: int, files: list[PendingFile], kind: int,
    ym: YandexMusic, uploads: UploadQueue,
) -> None:
    playlist = await ym.get_playlist(kind)
    if playlist is None:
        await bot.send_message(chat_id, "😕 Плейлист не найден — возможно, он удалён. Выберите другой: /target")
        return
    title = html.escape(playlist.title)
    status = await bot.send_message(chat_id, f"⏳ В очереди на загрузку в «{title}»: {len(files)}")

    async def update(text: str) -> None:
        try:
            await retry_telegram(lambda: status.edit_text(text))
        except TelegramBadRequest:
            pass

    ok: list[str] = []
    failed: list[str] = []
    async with uploads.upload_locks[user_id]:
        for i, pf in enumerate(files, 1):
            prefix = f"⏫ Загружаю в «{title}» {i}/{len(files)}: {html.escape(pf.file_name)}"
            try:
                await update(prefix + "\n(скачиваю из Telegram…)")
                data = await download_from_telegram(bot, pf.file_id)
                name, data, notes = await prepare_file(pf, data)
                await update(prefix + "\n(отправляю в Яндекс…)")
                await ym.upload_track(kind, name, data)
                ok.append(html.escape(name) + (f" <i>({html.escape('; '.join(notes))})</i>" if notes else ""))
            except (UploadError, ConversionError) as e:
                failed.append(f"{html.escape(pf.file_name)} — {html.escape(str(e))}")
            except Exception as e:
                log.exception("Ошибка загрузки %s", pf.file_name)
                failed.append(f"{html.escape(pf.file_name)} — {html.escape(type(e).__name__)}: {html.escape(str(e))}")

    lines = []
    if ok:
        lines.append(f"✅ Загружено в «{title}»: {len(ok)} из {len(files)}")
        lines += [f"• {x}" for x in ok]
        lines.append("\nЯндекс обрабатывает файлы пару минут, потом треки появятся в плейлисте.")
    if failed:
        lines.append(f"\n❌ Не загрузилось: {len(failed)}")
        lines += [f"• {x}" for x in failed]
    await update("\n".join(lines)[:4000])


@router.message(F.audio | F.document, _is_audio_message)
async def on_audio(message: Message, bot: Bot, ym: YandexMusic, store: Storage, config: Config,
                   uploads: UploadQueue) -> None:
    pf = pending_from_message(message)
    user_id = message.from_user.id
    if pf.file_size and pf.file_size > config.max_tg_download:
        limit = config.max_tg_download // (1024 * 1024)
        await message.reply(
            f"😕 Файл больше {limit} МБ — Telegram не даёт ботам скачивать такие файлы.\n"
            "Сожмите его или подключите локальный Bot API сервер (см. README, BOT_API_URL)."
        )
        return

    target = store.get_upload_target(user_id)
    if target is not None:
        await upload_files(bot, message.chat.id, user_id, [pf], target, ym, uploads)
        return

    # Несколько файлов, присланных разом, копятся в одну очередь с одним вопросом «куда загрузить?».
    async with uploads.queue_locks[user_id]:
        uploads.pending[user_id].append(pf)
        count = len(uploads.pending[user_id])
        text = f"📥 Файлов к загрузке: {count}\nВ какой плейлист их загрузить?"
        prompt = uploads.prompts.get(user_id)
        if prompt is not None:
            try:
                await retry_telegram(lambda: prompt.edit_text(text, reply_markup=prompt.reply_markup))
                return
            except TelegramBadRequest:
                pass
        playlists = await ym.get_my_playlists()
        uploads.prompts[user_id] = await message.answer(text, reply_markup=upload_targets(playlists))


@router.callback_query(UploadCb.filter(F.action == "to"))
async def on_upload_to(call: CallbackQuery, callback_data: UploadCb, bot: Bot, ym: YandexMusic,
                       uploads: UploadQueue) -> None:
    async with uploads.queue_locks[call.from_user.id]:
        files = uploads.take(call.from_user.id)
    if not files:
        await call.answer("Очередь пуста — пришлите файлы ещё раз", show_alert=True)
        return
    await call.answer()
    try:
        await call.message.delete()
    except TelegramBadRequest:
        pass  # сообщение слишком старое для удаления — не страшно
    await upload_files(bot, call.message.chat.id, call.from_user.id, files, callback_data.kind, ym, uploads)


@router.callback_query(UploadCb.filter(F.action == "new"))
async def on_upload_new(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await state.set_state(NewPlaylist.name)
    await state.update_data(for_upload=True)
    await call.message.answer("Как назвать новый плейлист? (/cancel — отмена)")


@router.callback_query(UploadCb.filter(F.action == "cancel"))
async def on_upload_cancel(call: CallbackQuery, uploads: UploadQueue) -> None:
    async with uploads.queue_locks[call.from_user.id]:
        files = uploads.take(call.from_user.id)
    await call.answer()
    await call.message.edit_text(f"Ок, {len(files)} файл(ов) не загружаю.")


@router.message(NewPlaylist.name, F.text, ~F.text.startswith("/"))
async def on_playlist_name(message: Message, state: FSMContext, bot: Bot, ym: YandexMusic,
                           uploads: UploadQueue) -> None:
    for_upload = (await state.get_data()).get("for_upload", False)
    await state.set_state(None)
    playlist = await ym.create_playlist(message.text.strip()[:100])
    await message.answer(f"✅ Плейлист «{html.escape(playlist.title)}» создан.")
    if not for_upload:
        return
    user_id = message.from_user.id
    async with uploads.queue_locks[user_id]:
        prompt = uploads.prompts.get(user_id)
        files = uploads.take(user_id)
    if prompt is not None:
        try:
            await prompt.delete()
        except TelegramBadRequest:
            pass
    if files:
        await upload_files(bot, message.chat.id, user_id, files, playlist.kind, ym, uploads)
