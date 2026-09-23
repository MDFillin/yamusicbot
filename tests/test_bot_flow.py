"""Сквозные сценарии через настоящий Dispatcher aiogram с подменёнными Telegram и Яндекс Музыкой."""

from __future__ import annotations

import itertools
from collections.abc import AsyncGenerator
from datetime import datetime
from types import SimpleNamespace as NS

import pytest
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import (
    AnswerCallbackQuery,
    DeleteMessage,
    EditMessageText,
    GetFile,
    SendAudio,
    SendMessage,
)
from aiogram.types import Audio, CallbackQuery, Chat, Document, File, Message, Update, User

from bot.audio import read_mp3_tags
from bot.callbacks import TrackCb, UploadCb
from bot.config import Config
from bot.main import build_dependencies, build_dispatcher
from bot.storage import Storage
from bot.ym import UploadResult

FAKE_MP3 = b"\xff\xfb\x90\x64" + b"\x00" * 2000
OWNER = User(id=1, is_bot=False, first_name="Owner")
STRANGER = User(id=666, is_bot=False, first_name="Stranger")
CHAT = Chat(id=1, type="private")


class FakeTelegram(BaseSession):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list = []
        self._ids = itertools.count(1000)

    async def close(self) -> None:
        pass

    async def stream_content(self, url, headers=None, timeout=30, chunk_size=65536,
                             raise_for_status=True) -> AsyncGenerator[bytes, None]:
        yield FAKE_MP3

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, (SendMessage, EditMessageText)):
            chat_id = getattr(method, "chat_id", None) or CHAT.id
            return Message(
                message_id=getattr(method, "message_id", None) or next(self._ids),
                date=datetime.now(), chat=Chat(id=chat_id, type="private"),
                text=method.text, reply_markup=method.reply_markup,
            ).as_(bot)
        if isinstance(method, GetFile):
            return File(file_id=method.file_id, file_unique_id="u", file_size=len(FAKE_MP3),
                        file_path=f"music/{method.file_id}.mp3")
        if isinstance(method, SendAudio):
            return Message(
                message_id=next(self._ids), date=datetime.now(), chat=CHAT,
                audio=Audio(file_id="AUDIO-1", file_unique_id="a1", duration=method.duration or 0),
            ).as_(bot)
        if isinstance(method, (AnswerCallbackQuery, DeleteMessage)):
            return True
        raise AssertionError(f"Неожиданный запрос к Telegram: {type(method).__name__}")

    def texts(self) -> list[str]:
        return [c.text for c in self.calls if isinstance(c, (SendMessage, EditMessageText))]


def make_track():
    album = NS(id=1, title="Звезда по имени Солнце", year=1989)
    return NS(id=123, title="Кукушка", version=None, artists=[NS(name="Кино")], duration_ms=400_000,
              albums=[album], available=True)


class FakeYM:
    uid = 42
    login = "me"
    has_plus = True

    def __init__(self) -> None:
        self.uploads: list[tuple[int, str, bytes]] = []
        self.playlist = NS(kind=1003, title="Мои записи", track_count=2)

    async def get_my_playlists(self):
        return [self.playlist]

    async def get_playlist(self, kind, owner=None):
        return self.playlist if int(kind) == self.playlist.kind else None

    async def upload_track(self, kind, filename, data):
        self.uploads.append((kind, filename, data))
        return UploadResult("ugc-1", "CREATED")

    downloads = 0

    async def get_track(self, track_id):
        return make_track()

    async def download(self, track):
        self.downloads += 1
        return FAKE_MP3, 320

    async def download_cover(self, track, size="400x400"):
        return b"\xff\xd8cover"

    async def search(self, query, type_):
        track = make_track()
        return NS(misspell_corrected=False, misspell_result=None, tracks=NS(results=[track]),
                  albums=None, playlists=None, artists=None)


_dispatcher = None


@pytest.fixture
def env(tmp_path):
    global _dispatcher
    telegram = FakeTelegram()
    bot = Bot("42:TEST", session=telegram, default=DefaultBotProperties(parse_mode="HTML"))
    ym = FakeYM()
    store = Storage(tmp_path / "storage.json")
    config = Config(bot_token="42:TEST", ym_token="x", allowed_users=frozenset({OWNER.id}), data_dir=tmp_path)
    # Роутеры aiogram подключаются к диспетчеру только один раз, поэтому между тестами меняем лишь зависимости.
    if _dispatcher is None:
        _dispatcher = build_dispatcher(config, bot, ym, store)
    fresh = build_dependencies(config, bot, ym, store)
    _dispatcher.workflow_data.update(fresh)
    _dispatcher.fsm.storage = MemoryStorage()
    return NS(dp=_dispatcher, bot=bot, tg=telegram, ym=ym, store=store)


_update_ids = itertools.count(1)


def message_update(user: User = OWNER, **fields) -> Update:
    msg = Message(message_id=next(_update_ids), date=datetime.now(), chat=CHAT, from_user=user, **fields)
    return Update(update_id=next(_update_ids), message=msg)


def audio_update(file_id: str, caption: str | None = None) -> Update:
    audio = Audio(file_id=file_id, file_unique_id=file_id, duration=100, file_name="rec.mp3", file_size=5000)
    return message_update(audio=audio, caption=caption)


async def test_stranger_is_rejected(env):
    await env.dp.feed_update(env.bot, message_update(user=STRANGER, text="кино"))
    doc = Document(file_id="S1", file_unique_id="S1", file_name="x.mp3", mime_type="audio/mpeg", file_size=10)
    await env.dp.feed_update(env.bot, message_update(user=STRANGER, document=doc))
    texts = env.tg.texts()
    assert len(texts) == 2, "ни поиска, ни вопроса о загрузке — только отказ"
    assert all("приватный" in t and "666" in t for t in texts)
    assert env.ym.uploads == []


async def test_start_shows_account(env):
    await env.dp.feed_update(env.bot, message_update(text="/start"))
    assert "Аккаунт Яндекса: <b>me</b>" in env.tg.texts()[0]


async def test_search_shows_download_buttons(env):
    await env.dp.feed_update(env.bot, message_update(text="кино кукушка"))
    sent = [c for c in env.tg.calls if isinstance(c, SendMessage)][0]
    buttons = [b for row in sent.reply_markup.inline_keyboard for b in row]
    assert any(b.callback_data == "t:dl:123" and "Кино — Кукушка" in b.text for b in buttons)


async def test_upload_asks_playlist_then_uploads_all_files(env):
    await env.dp.feed_update(env.bot, audio_update("F1", caption="Кино - Кукушка"))
    await env.dp.feed_update(env.bot, audio_update("F2"))

    prompts = [c for c in env.tg.calls if isinstance(c, SendMessage)]
    assert len(prompts) == 1, "на несколько файлов — один вопрос"
    assert "Файлов к загрузке: 2" in env.tg.texts()[-1]
    assert env.ym.uploads == []

    prompt = Message(message_id=1000, date=datetime.now(), chat=CHAT, text="?")
    call = CallbackQuery(id="c1", from_user=OWNER, chat_instance="ci", message=prompt,
                         data=UploadCb(action="to", kind=1003).pack())
    await env.dp.feed_update(env.bot, Update(update_id=next(_update_ids), callback_query=call))

    assert [(k, n) for k, n, _ in env.ym.uploads] == [(1003, "Кино - Кукушка.mp3"), (1003, "rec.mp3")]
    assert read_mp3_tags(env.ym.uploads[0][2]) == ("Кино", "Кукушка")
    assert "✅ Загружено в «Мои записи»: 2 из 2" in env.tg.texts()[-1]


async def test_upload_goes_straight_to_default_playlist(env):
    env.store.set_upload_target(OWNER.id, 1003)
    doc = Document(file_id="D1", file_unique_id="D1", file_name="demo.mp3", mime_type="audio/mpeg", file_size=10)
    await env.dp.feed_update(env.bot, message_update(document=doc))
    assert [(k, n) for k, n, _ in env.ym.uploads] == [(1003, "demo.mp3")]


async def test_non_audio_document_is_not_uploaded(env):
    doc = Document(file_id="D2", file_unique_id="D2", file_name="notes.pdf", mime_type="application/pdf")
    await env.dp.feed_update(env.bot, message_update(document=doc))
    assert env.ym.uploads == []
    assert "Не понял" in env.tg.texts()[-1]


async def test_download_track_sends_tagged_mp3_and_caches_file_id(env):
    def click():
        call = CallbackQuery(id="c2", from_user=OWNER, chat_instance="ci",
                             data=TrackCb(action="dl", track="123").pack(),
                             message=Message(message_id=5, date=datetime.now(), chat=CHAT, text="?"))
        return Update(update_id=next(_update_ids), callback_query=call)

    await env.dp.feed_update(env.bot, click())
    first = [c for c in env.tg.calls if isinstance(c, SendAudio)][0]
    assert first.title == "Кукушка" and first.performer == "Кино" and first.duration == 400
    assert first.audio.filename == "Кино - Кукушка.mp3"
    assert read_mp3_tags(first.audio.data) == ("Кино", "Кукушка")
    assert "Звезда по имени Солнце, 1989" in first.caption

    await env.dp.feed_update(env.bot, click())
    second = [c for c in env.tg.calls if isinstance(c, SendAudio)][1]
    assert second.audio == "AUDIO-1", "повторно трек отправляется по file_id без скачивания"
    assert env.ym.downloads == 1
