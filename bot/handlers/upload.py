"""Загрузка своих аудиофайлов в Яндекс Музыку: очередь, редактор данных трека, выбор плейлиста."""

from __future__ import annotations

import asyncio
import html
import io
import itertools
import logging
import secrets
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import aiohttp
from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from bot.admin import Admin, LimitReached
from bot.audio import (
    ConversionError,
    TrackMeta,
    clean_year,
    is_audio_filename,
    normalize_cover,
    parse_caption,
    prepare_for_upload,
    read_tags,
    safe_filename,
)
from bot.callbacks import EditCb, UploadCb
from bot.config import Config
from bot.errors import describe_error
from bot.keyboards import short, track_editor, upload_card
from bot.placer import TopPlacer
from bot.sender import retry_telegram
from bot.states import EditTrack, NewPlaylist
from bot.storage import Storage
from bot.ym import UploadError, YandexMusic

log = logging.getLogger(__name__)
router = Router(name="upload")

# Фоновые задачи «сообщить, что треки встали в начало плейлиста».
_reports: set[asyncio.Task] = set()
MAX_QUEUE = 30  # файлов в очереди у одного человека: бот открыт всем, а очередь хранится в памяти

FIELDS = {
    "title": ("🎵", "Название", "новое название"),
    "artist": ("👤", "Исполнитель", "исполнителя (несколько — через запятую)"),
    "album": ("💿", "Альбом", "название альбома"),
    "year": ("📅", "Год", "год, например 2024"),
}


@dataclass
class QueuedFile:
    """Файл из чата, который ждёт загрузки: что прислали, что поправил пользователь, что внутри файла."""

    pid: int
    file_id: str
    file_name: str
    file_size: int | None
    caption: str | None = None
    performer: str | None = None  # метаданные Telegram
    title: str | None = None
    edit: TrackMeta = field(default_factory=TrackMeta)
    data: bytes | None = field(default=None, repr=False)  # скачивается, когда открыли редактор
    tags: TrackMeta | None = None  # теги самого файла

    def __post_init__(self) -> None:
        if (parsed := parse_caption(self.caption)) is not None:
            self.edit.artist, self.edit.title = parsed

    def fallbacks(self) -> tuple[str | None, str | None]:
        """(исполнитель, название), если в файле своих тегов нет: из Telegram или имени файла."""
        stem = Path(self.file_name).stem.replace("_", " ").strip()
        guessed = parse_caption(stem)
        artist = self.performer or (guessed[0] if guessed else None)
        title = self.title or (guessed[1] if guessed else stem or None)
        return artist, title

    def effective(self) -> TrackMeta:
        """Что окажется в треке после загрузки (для показа в редакторе)."""
        tags = self.tags or TrackMeta()
        artist, title = self.fallbacks()

        def pick(edited: str | None, from_file: str | None) -> str | None:
            return from_file if edited is None else (edited or None)

        return TrackMeta(
            title=pick(self.edit.title, tags.title) or title,
            artist=pick(self.edit.artist, tags.artist) or artist,
            album=pick(self.edit.album, tags.album),
            year=pick(self.edit.year, tags.year),
            cover=None if self.edit.remove_cover else (self.edit.cover or tags.cover),
        )

    def label(self) -> str:
        meta = self.effective()
        if meta.artist and meta.title:
            return f"{meta.artist} — {meta.title}"
        return meta.title or self.file_name


class UploadQueue:
    """Файлы, ждущие загрузки, по пользователям: карточка очереди и открытый редактор."""

    def __init__(self, admin: Admin | None = None) -> None:
        self.admin = admin  # дневные лимиты и статистика загрузок
        self.pending: dict[int, list[QueuedFile]] = defaultdict(list)
        self.prompts: dict[int, Message] = {}
        self.editors: dict[int, Message] = {}
        self.choosing: set[int] = set()  # пользователь нажал «Другой плейлист»
        self.queue_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.upload_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._pids = itertools.count(1)

    def next_pid(self) -> int:
        return next(self._pids)

    def find(self, user_id: int, pid: int) -> QueuedFile | None:
        return next((f for f in self.pending.get(user_id, []) if f.pid == pid), None)

    def take(self, user_id: int) -> list[QueuedFile]:
        self.prompts.pop(user_id, None)
        self.choosing.discard(user_id)
        return self.pending.pop(user_id, [])


def queued_from_message(message: Message, pid: int) -> QueuedFile | None:
    if message.audio:
        a = message.audio
        name = a.file_name or safe_filename(" - ".join(x for x in (a.performer, a.title) if x) or "track") + ".mp3"
        return QueuedFile(pid, a.file_id, name, a.file_size, message.caption, a.performer, a.title)
    doc = message.document
    if doc and ((doc.mime_type or "").startswith("audio/") or is_audio_filename(doc.file_name)):
        return QueuedFile(pid, doc.file_id, doc.file_name or "track.mp3", doc.file_size, message.caption)
    return None


def _is_audio_message(message: Message) -> bool:
    return queued_from_message(message, 0) is not None


def _is_image_message(message: Message) -> bool:
    doc = message.document
    return bool(message.photo) or bool(doc and (doc.mime_type or "").startswith("image/"))


async def prepare_file(qf: QueuedFile, data: bytes) -> tuple[str, bytes, list[str]]:
    """Готовит присланный в чат файл: правки пользователя важнее тегов файла, а те — метаданных Telegram."""
    artist, title = qf.fallbacks()
    return await prepare_for_upload(qf.file_name, data, edit=qf.edit, fallback_artist=artist, fallback_title=title)


async def download_from_telegram(bot: Bot, file_id: str) -> bytes:
    buf = io.BytesIO()
    try:
        await bot.download(file_id, destination=buf, timeout=600)
    except (aiohttp.ClientError, TimeoutError) as e:  # в тексте таких ошибок адрес файла вместе с токеном бота
        raise UploadError(f"Не удалось скачать файл из Telegram: {describe_error(e)}") from e
    return buf.getvalue()


async def _delete(message: Message | None) -> None:
    if message is None:
        return
    try:
        await message.delete()
    except TelegramBadRequest:
        pass  # уже удалено или слишком старое


# ---------- карточка очереди ----------

async def show_queue(bot: Bot, chat_id: int, user_id: int, ym: YandexMusic, store: Storage, uploads: UploadQueue,
                     *, resend: bool = False) -> None:
    """Показывает (или обновляет) карточку с файлами, которые ждут загрузки."""
    files = uploads.pending.get(user_id, [])
    old = uploads.prompts.get(user_id)
    if not files:
        await _delete(old)
        uploads.prompts.pop(user_id, None)
        return
    playlists = await ym.get_my_playlists()
    target_kind = None if user_id in uploads.choosing else store.get_upload_target(user_id)
    target = next((p for p in playlists if p.kind == target_kind), None)

    lines = [f"📥 <b>Готово к загрузке: {len(files)}</b>"]
    for n, qf in enumerate(files, 1):
        mark = " ✏️" if qf.edit.changed else ""
        lines.append(f"{n}. {html.escape(short(qf.label(), 70))}{mark}")
    lines.append("")
    lines.append("Название, исполнителя, альбом, год и обложку можно поправить кнопкой ✏️.")
    if target is not None:
        lines.append(f"Плейлист: «{html.escape(target.title)}»")
    else:
        lines.append("В какой плейлист загрузить?")
    text = "\n".join(lines)
    markup = upload_card([(qf.pid, qf.label()) for qf in files], playlists, target)

    if old is not None and not resend:
        try:
            uploads.prompts[user_id] = await retry_telegram(lambda: old.edit_text(text, reply_markup=markup))
            return
        except TelegramBadRequest as e:
            if "message is not modified" in str(e):
                return
    await _delete(old)
    uploads.prompts[user_id] = await bot.send_message(chat_id, text, reply_markup=markup)


@router.message(F.audio | F.document, _is_audio_message)
async def on_audio(message: Message, bot: Bot, ym: YandexMusic, store: Storage, config: Config,
                   uploads: UploadQueue) -> None:
    user_id = message.from_user.id
    qf = queued_from_message(message, uploads.next_pid())
    if qf.file_size and qf.file_size > config.max_tg_download:
        limit = config.max_tg_download // (1024 * 1024)
        await message.reply(
            f"😕 Файл больше {limit} МБ — Telegram не даёт ботам скачивать такие файлы.\n"
            "Загрузите его через «Медиатеку» (там лимита нет), сожмите или подключите свой Bot API сервер "
            "(см. README, BOT_API_URL)."
        )
        return
    # Файлы, присланные разом, копятся в одну очередь с одной карточкой.
    async with uploads.queue_locks[user_id]:
        if len(uploads.pending[user_id]) >= MAX_QUEUE:
            await message.reply(f"😕 В очереди уже {MAX_QUEUE} файлов — сначала загрузите или отмените их.")
            return
        uploads.pending[user_id].append(qf)
        await show_queue(bot, message.chat.id, user_id, ym, store, uploads)


@router.callback_query(UploadCb.filter(F.action == "pick"))
async def on_pick_playlist(call: CallbackQuery, bot: Bot, ym: YandexMusic, store: Storage,
                           uploads: UploadQueue) -> None:
    await call.answer()
    uploads.choosing.add(call.from_user.id)
    await show_queue(bot, call.message.chat.id, call.from_user.id, ym, store, uploads)


@router.callback_query(UploadCb.filter(F.action == "to"))
async def on_upload_to(call: CallbackQuery, callback_data: UploadCb, bot: Bot, ym: YandexMusic,
                       uploads: UploadQueue, placer: TopPlacer) -> None:
    user_id = call.from_user.id
    async with uploads.queue_locks[user_id]:
        files = uploads.take(user_id)
        editor = uploads.editors.pop(user_id, None)
    if not files:
        await call.answer("Очередь пуста — пришлите файлы ещё раз", show_alert=True)
        return
    await call.answer()
    await _delete(call.message)
    await _delete(editor)
    await upload_files(bot, call.message.chat.id, user_id, files, callback_data.kind, ym, uploads, placer)


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
        editor = uploads.editors.pop(call.from_user.id, None)
    await call.answer()
    await _delete(editor)
    await call.message.edit_text(f"Ок, файлов не загружаю: {len(files)}.")


@router.message(NewPlaylist.name, F.text, ~F.text.startswith("/"))
async def on_playlist_name(message: Message, state: FSMContext, bot: Bot, ym: YandexMusic,
                           uploads: UploadQueue, placer: TopPlacer) -> None:
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
        editor = uploads.editors.pop(user_id, None)
    await _delete(prompt)
    await _delete(editor)
    if files:
        await upload_files(bot, message.chat.id, user_id, files, playlist.kind, ym, uploads, placer)


# ---------- редактор данных трека ----------

def editor_text(qf: QueuedFile) -> str:
    meta = qf.effective()
    size = f" · {qf.file_size / 1048576:.1f} МБ" if qf.file_size else ""

    def row(name: str, value: str | None, edited: bool) -> str:
        icon, label, _ = FIELDS[name]
        shown = f"<b>{html.escape(value)}</b>" if value else "—"
        return f"{icon} {label}: {shown}{' ✏️' if edited else ''}"

    if qf.edit.cover:
        cover = "новая ✏️"
    elif qf.edit.remove_cover:
        cover = "без обложки ✏️"
    else:
        cover = "из файла" if meta.cover else "нет"
    return "\n".join([
        "✏️ <b>Данные трека</b>",
        f"<i>{html.escape(qf.file_name)}{size}</i>",
        "",
        row("title", meta.title, qf.edit.title is not None),
        row("artist", meta.artist, qf.edit.artist is not None),
        row("album", meta.album, qf.edit.album is not None),
        row("year", meta.year, qf.edit.year is not None),
        f"🖼 Обложка: {cover}",
        "",
        "Нажмите, что поменять. Когда всё готово — «✅ Готово».",
    ])


async def show_editor(bot: Bot, chat_id: int, user_id: int, qf: QueuedFile, uploads: UploadQueue) -> None:
    """Карточка редактора: с обложкой — фото с подписью, без неё — текст. Всегда внизу чата."""
    await _delete(uploads.editors.pop(user_id, None))
    meta = qf.effective()
    markup = track_editor(qf.pid, has_cover=bool(meta.cover))
    text = editor_text(qf)
    if meta.cover:
        photo = BufferedInputFile(meta.cover, filename="cover.jpg")
        try:
            uploads.editors[user_id] = await bot.send_photo(chat_id, photo, caption=text, reply_markup=markup)
            return
        except TelegramBadRequest:
            log.info("Telegram не принял обложку как фото, показываю редактор текстом")
    uploads.editors[user_id] = await bot.send_message(chat_id, text, reply_markup=markup)


async def _load_file(bot: Bot, qf: QueuedFile, uploads: UploadQueue, user_id: int) -> None:
    for other in uploads.pending.get(user_id, []):
        if other is not qf:
            other.data = None  # в памяти держим только файл из открытого редактора (теги и обложка остаются)
    if qf.data is None:
        qf.data = await download_from_telegram(bot, qf.file_id)
        qf.tags = read_tags(qf.data)


@router.callback_query(EditCb.filter(F.action == "open"))
async def on_edit_open(call: CallbackQuery, callback_data: EditCb, bot: Bot, uploads: UploadQueue) -> None:
    qf = uploads.find(call.from_user.id, callback_data.pid)
    if qf is None:
        await call.answer("Этого файла уже нет в очереди", show_alert=True)
        return
    await call.answer("⏳ Читаю данные трека…")
    await _load_file(bot, qf, uploads, call.from_user.id)
    await show_editor(bot, call.message.chat.id, call.from_user.id, qf, uploads)


@router.callback_query(EditCb.filter(F.action.in_(set(FIELDS))))
async def on_edit_field(call: CallbackQuery, callback_data: EditCb, state: FSMContext, uploads: UploadQueue) -> None:
    qf = uploads.find(call.from_user.id, callback_data.pid)
    if qf is None:
        await call.answer("Этого файла уже нет в очереди", show_alert=True)
        return
    await call.answer()
    name = callback_data.action
    icon, label, what = FIELDS[name]
    current = getattr(qf.effective(), name)
    hint = "«-» — вернуть как в файле" if name in ("title", "artist") else "«-» — очистить"
    now = f"\nСейчас: <code>{html.escape(current)}</code>" if current else ""
    prompt = await call.message.answer(f"{icon} Пришлите {what}.{now}\n{hint}, /cancel — отмена.")
    await state.set_state(EditTrack.text)
    await state.update_data(pid=qf.pid, field=name, prompt_id=prompt.message_id)


@router.message(EditTrack.text, F.text, ~F.text.startswith("/"))
async def on_edit_value(message: Message, state: FSMContext, bot: Bot, uploads: UploadQueue) -> None:
    data = await state.get_data()
    qf = uploads.find(message.from_user.id, data.get("pid", 0))
    name = data.get("field")
    if qf is None or name not in FIELDS:
        await state.set_state(None)
        await message.answer("Этого файла уже нет в очереди.")
        return
    value = message.text.strip()[:200]
    if value == "-":
        value = None if name in ("title", "artist") else ""
    elif name == "year":
        try:
            value = clean_year(value)
        except ValueError as e:
            await message.answer(f"😕 {e}. Попробуйте ещё раз или /cancel.")
            return
    setattr(qf.edit, name, value)
    await state.set_state(None)
    await bot_cleanup(bot, message, data.get("prompt_id"))
    await show_editor(bot, message.chat.id, message.from_user.id, qf, uploads)


async def bot_cleanup(bot: Bot, answer: Message, prompt_id: int | None) -> None:
    """Убирает вопрос бота и ответ пользователя, чтобы редактор не терялся в переписке."""
    if prompt_id:
        try:
            await bot.delete_message(answer.chat.id, prompt_id)
        except TelegramBadRequest:
            pass
    await _delete(answer)


@router.callback_query(EditCb.filter(F.action == "cover"))
async def on_edit_cover(call: CallbackQuery, callback_data: EditCb, state: FSMContext, uploads: UploadQueue) -> None:
    qf = uploads.find(call.from_user.id, callback_data.pid)
    if qf is None:
        await call.answer("Этого файла уже нет в очереди", show_alert=True)
        return
    await call.answer()
    prompt = await call.message.answer("🖼 Пришлите картинку для обложки — фото или файл JPEG/PNG. /cancel — отмена.")
    await state.set_state(EditTrack.cover)
    await state.update_data(pid=qf.pid, prompt_id=prompt.message_id)


@router.message(EditTrack.cover, _is_image_message)
async def on_cover_image(message: Message, state: FSMContext, bot: Bot, uploads: UploadQueue) -> None:
    data = await state.get_data()
    qf = uploads.find(message.from_user.id, data.get("pid", 0))
    if qf is None:
        await state.set_state(None)
        await message.answer("Этого файла уже нет в очереди.")
        return
    file_id = message.photo[-1].file_id if message.photo else message.document.file_id
    try:
        cover = await normalize_cover(await download_from_telegram(bot, file_id))
    except ConversionError as e:
        await message.answer(f"😕 {html.escape(str(e))}")
        return
    qf.edit.cover, qf.edit.remove_cover = cover, False
    await state.set_state(None)
    await bot_cleanup(bot, message, data.get("prompt_id"))
    await show_editor(bot, message.chat.id, message.from_user.id, qf, uploads)


@router.message(EditTrack.cover, ~F.text.startswith("/"))
async def on_cover_not_image(message: Message) -> None:
    await message.answer("Нужна картинка: пришлите фото или файл JPEG/PNG. /cancel — отмена.")


@router.callback_query(EditCb.filter(F.action == "nocover"))
async def on_edit_nocover(call: CallbackQuery, callback_data: EditCb, bot: Bot, uploads: UploadQueue) -> None:
    qf = uploads.find(call.from_user.id, callback_data.pid)
    if qf is None:
        await call.answer("Этого файла уже нет в очереди", show_alert=True)
        return
    qf.edit.cover, qf.edit.remove_cover = None, True
    await call.answer("Обложка будет убрана")
    await show_editor(bot, call.message.chat.id, call.from_user.id, qf, uploads)


@router.callback_query(EditCb.filter(F.action == "done"))
async def on_edit_done(call: CallbackQuery, state: FSMContext, bot: Bot, ym: YandexMusic, store: Storage,
                       uploads: UploadQueue) -> None:
    await call.answer("Сохранено")
    if await state.get_state() in (EditTrack.text.state, EditTrack.cover.state):
        await state.set_state(None)
    user_id = call.from_user.id
    await _delete(uploads.editors.pop(user_id, None))
    async with uploads.queue_locks[user_id]:
        await show_queue(bot, call.message.chat.id, user_id, ym, store, uploads, resend=True)


# ---------- загрузка ----------

async def upload_files(
    bot: Bot, chat_id: int, user_id: int, files: list[QueuedFile], kind: int,
    ym: YandexMusic, uploads: UploadQueue, placer: TopPlacer,
) -> None:
    playlist = await ym.get_playlist(kind)
    if playlist is None:
        await bot.send_message(chat_id, "😕 Плейлист не найден — возможно, он удалён. Пришлите файлы ещё раз.")
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
    placed: list[asyncio.Future] = []
    async with uploads.upload_locks[user_id]:
        for i, qf in enumerate(files, 1):
            prefix = f"⏫ Загружаю в «{title}» {i}/{len(files)}: {html.escape(qf.label())}"
            try:
                if uploads.admin is not None:
                    uploads.admin.check_limit(user_id, "upload")
                if qf.data is None:
                    await update(prefix + "\n(скачиваю из Telegram…)")
                data = qf.data or await download_from_telegram(bot, qf.file_id)
                name, data, notes = await prepare_file(qf, data)
                await update(prefix + "\n(отправляю в Яндекс…)")
                known = await placer.before_upload(ym, kind)
                result = await ym.upload_track(kind, name, data)
                placed.append(placer.after_upload(ym, kind, known, result.ugc_track_id))
                if uploads.admin is not None:
                    uploads.admin.count(user_id, "upload")
                if result.note:
                    notes.append(result.note)
                ok.append(html.escape(name) + (f" <i>({html.escape('; '.join(notes))})</i>" if notes else ""))
            except (UploadError, ConversionError, LimitReached) as e:
                failed.append(f"{html.escape(qf.file_name)} — {html.escape(str(e))}")
            except Exception as e:
                ref = secrets.token_hex(3)
                log.exception("Ошибка загрузки %s [%s]", qf.file_name, ref)
                failed.append(f"{html.escape(qf.file_name)} — {html.escape(describe_error(e))} (код {ref})")
            finally:
                qf.data = None  # байты больше не нужны

    lines = []
    if ok:
        lines.append(f"✅ Загружено в «{title}»: {len(ok)} из {len(files)}")
        lines += [f"• {x}" for x in ok]
    if failed:
        lines.append(f"\n❌ Не загрузилось: {len(failed)}")
        lines += [f"• {x}" for x in failed]
    text = "\n".join(lines)
    if not placed:
        await update(text[:4000])
        return
    waiting = "\n\n⏳ Яндекс обработает файлы за пару минут — потом я подниму их в начало плейлиста."
    await update((text + waiting)[:4000])
    task = asyncio.create_task(_report_placement(status, text, title, placed))
    _reports.add(task)
    task.add_done_callback(_reports.discard)


async def _report_placement(status: Message, text: str, title: str, placed: list[asyncio.Future]) -> None:
    results = await asyncio.gather(*placed)
    if all(results):
        note = f"\n\n📌 Готово: треки уже в начале плейлиста «{title}»."
    elif any(results):
        note = "\n\n📌 Часть треков поднята в начало плейлиста, остальные Яндекс оставил в конце."
    else:
        note = "\n\n⚠️ Треки в плейлисте, но поднять их в начало не получилось — они в конце."
    try:
        await retry_telegram(lambda: status.edit_text((text + note)[:4000]))
    except TelegramBadRequest:
        pass
