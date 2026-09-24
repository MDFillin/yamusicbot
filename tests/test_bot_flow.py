"""Сквозные сценарии через настоящий Dispatcher aiogram с подменёнными Telegram и Яндекс Музыкой."""

from __future__ import annotations

import asyncio
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
    AnswerInlineQuery,
    DeleteMessage,
    EditMessageMedia,
    EditMessageText,
    GetFile,
    GetMe,
    SendAudio,
    SendDocument,
    SendMessage,
    SendPhoto,
)
from aiogram.types import (
    Audio,
    CallbackQuery,
    Chat,
    ChosenInlineResult,
    Document,
    File,
    InlineQuery,
    Message,
    PhotoSize,
    Update,
    User,
)

from bot import accounts as accounts_module
from bot.accounts import Accounts
from bot.audio import read_mp3_tags, read_tags, tag_mp3
from bot.callbacks import EditCb, MenuCb, SettingsCb, TrackCb, UploadCb
from bot.config import Config
from bot.main import build_dependencies, build_dispatcher
from bot.storage import Storage
from bot.ym import UploadResult, YandexNotReady

FAKE_MP3 = b"\xff\xfb\x90\x64" + b"\x00" * 2000
FAKE_JPEG = b"\xff\xd8\xff\xe0" + b"cover" * 40
OWNER = User(id=1, is_bot=False, first_name="Owner")
FRIEND = User(id=2, is_bot=False, first_name="Friend")
NEWBIE = User(id=666, is_bot=False, first_name="Новичок")


class FakeTelegram(BaseSession):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list = []
        self._ids = itertools.count(1000)

    async def close(self) -> None:
        pass

    async def stream_content(self, url, headers=None, timeout=30, chunk_size=65536,
                             raise_for_status=True) -> AsyncGenerator[bytes, None]:
        yield FAKE_JPEG if "/photos/" in url else FAKE_MP3

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, (SendMessage, EditMessageText)):
            chat_id = getattr(method, "chat_id", None) or 1
            return Message(
                message_id=getattr(method, "message_id", None) or next(self._ids),
                date=datetime.now(), chat=Chat(id=chat_id, type="private"),
                text=method.text, reply_markup=method.reply_markup,
            ).as_(bot)
        if isinstance(method, GetFile):
            path = f"photos/{method.file_id}.jpg" if method.file_id.startswith("PH") else f"music/{method.file_id}.mp3"
            return File(file_id=method.file_id, file_unique_id="u", file_size=len(FAKE_MP3), file_path=path)
        if isinstance(method, SendPhoto):
            chat = Chat(id=method.chat_id, type="private")
            return Message(message_id=next(self._ids), date=datetime.now(), chat=chat,
                           caption=method.caption, reply_markup=method.reply_markup).as_(bot)
        if isinstance(method, SendAudio):
            return Message(
                message_id=next(self._ids), date=datetime.now(), chat=Chat(id=method.chat_id, type="private"),
                audio=Audio(file_id="AUDIO-1", file_unique_id="a1", duration=method.duration or 0),
            ).as_(bot)
        if isinstance(method, (AnswerCallbackQuery, DeleteMessage, AnswerInlineQuery, EditMessageMedia)):
            return True
        if isinstance(method, GetMe):
            return User(id=42, is_bot=True, first_name="Bot", username="testbot", supports_inline_queries=True)
        if isinstance(method, SendDocument):
            return Message(message_id=next(self._ids), date=datetime.now(),
                           chat=Chat(id=method.chat_id, type="private"), caption=method.caption).as_(bot)
        raise AssertionError(f"Неожиданный запрос к Telegram: {type(method).__name__}")

    def texts(self) -> list[str]:
        return [c.text if not isinstance(c, SendPhoto) else c.caption for c in self.calls
                if isinstance(c, (SendMessage, EditMessageText, SendPhoto))]

    def buttons(self) -> list:
        markup = [c.reply_markup for c in self.calls if isinstance(c, (SendMessage, EditMessageText, SendPhoto))][-1]
        return [b for row in markup.inline_keyboard for b in row] if markup else []

    def button(self, prefix: str):
        """Кнопка из последнего сообщения, чей текст начинается с prefix."""
        return next(b for b in self.buttons() if b.text.startswith(prefix))


def make_track():
    album = NS(id=1, title="Звезда по имени Солнце", year=1989)
    return NS(id=123, title="Кукушка", version=None, artists=[NS(name="Кино")], duration_ms=400_000,
              albums=[album], available=True, cover_uri="avatars.yandex.net/get-music-content/x/%%")


class FakeYM:
    has_plus = True
    start_error = None

    @property
    def ready(self) -> bool:
        return self.start_error is None

    def __init__(self, uid: int = 42, login: str = "me") -> None:
        self.uid, self.login = uid, login
        self.uploads: list[tuple[int, str, bytes]] = []
        self.searches: list[str] = []
        self.playlist = NS(kind=1003, title="Мои записи", track_count=2)
        self.downloads = 0
        self.bitrates: list[int | None] = []

    async def start(self):
        pass

    async def ensure_started(self):
        if self.start_error:
            raise YandexNotReady(self.start_error)

    async def close(self):
        pass

    async def get_my_playlists(self):
        return [self.playlist]

    async def get_playlist(self, kind, owner=None):
        return self.playlist if int(kind) == self.playlist.kind else None

    async def upload_track(self, kind, filename, data):
        self.uploads.append((kind, filename, data))
        return UploadResult("ugc-1", "CREATED")

    async def get_track(self, track_id):
        return make_track()

    async def download_tagged(self, track, max_bitrate=None):
        self.downloads += 1
        self.bitrates.append(max_bitrate)
        return tag_mp3(FAKE_MP3, artist="Кино", title=track.title), max_bitrate or 320

    async def download_cover(self, track, size="400x400"):
        return b"\xff\xd8cover"

    async def get_liked_track_ids(self):
        return ["123:1"]

    async def get_tracks(self, ids):
        return [make_track() for _ in ids]

    async def search(self, query, type_, page=0):
        self.searches.append(query)
        return NS(misspell_corrected=False, misspell_result=None, tracks=NS(results=[make_track()], total=1),
                  albums=None, playlists=None, artists=None)


class FakePlacer:
    """Вместо ожидания обработки у Яндекса: сразу «ставит трек наверх»."""

    def __init__(self) -> None:
        self.placed: list[tuple[int, int, str | None]] = []

    async def before_upload(self, ym, kind):
        return set()

    def after_upload(self, ym, kind, known, ugc_id):
        self.placed.append((ym.uid, kind, ugc_id))
        future = asyncio.get_running_loop().create_future()
        future.set_result(True)
        return future


class FakeOAuth:
    async def request_device_code(self, device_name=None):
        return NS(device_code="dev", user_code="ABCD1234", verification_url="https://ya.ru/device",
                  expires_in=300, interval=0)

    async def poll_device_token(self, device_code):
        return NS(access_token="tok-newbie")


_dispatcher = None


@pytest.fixture
def env(tmp_path, monkeypatch):
    global _dispatcher
    monkeypatch.setattr(accounts_module, "MIN_POLL_INTERVAL", 0.01)
    telegram = FakeTelegram()
    bot = Bot("42:TEST", session=telegram, default=DefaultBotProperties(parse_mode="HTML"))
    fakes = {"tok-owner": FakeYM(42, "me"), "tok-friend": FakeYM(77, "friend"), "tok-newbie": FakeYM(99, "newbie")}
    store = Storage(tmp_path / "bot.db")
    store.set_account(OWNER.id, "tok-owner", "me")
    store.set_account(FRIEND.id, "tok-friend", "friend")
    accounts = Accounts(store, factory=fakes.__getitem__, oauth=FakeOAuth())
    config = Config(bot_token="42:TEST", data_dir=tmp_path, admin_ids=frozenset({OWNER.id}))
    # Роутеры aiogram подключаются к диспетчеру только один раз, поэтому между тестами меняем лишь зависимости.
    placer = FakePlacer()
    if _dispatcher is None:
        _dispatcher = build_dispatcher(config, bot, accounts, store)
    _dispatcher.workflow_data.update(build_dependencies(config, bot, accounts, store, placer))
    _dispatcher.fsm.storage = MemoryStorage()
    yield NS(dp=_dispatcher, bot=bot, tg=telegram, ym=fakes["tok-owner"], friend=fakes["tok-friend"],
             newbie=fakes["tok-newbie"], store=store, accounts=accounts, placer=placer)
    store.close()


_update_ids = itertools.count(1)


def message_update(user: User = OWNER, **fields) -> Update:
    msg = Message(message_id=next(_update_ids), date=datetime.now(), chat=Chat(id=user.id, type="private"),
                  from_user=user, **fields)
    return Update(update_id=next(_update_ids), message=msg)


def callback_update(data: str, user: User = OWNER) -> Update:
    chat = Chat(id=user.id, type="private")
    message = Message(message_id=next(_update_ids), date=datetime.now(), chat=chat, text="?")
    call = CallbackQuery(id=str(next(_update_ids)), from_user=user, chat_instance="ci", message=message, data=data)
    return Update(update_id=next(_update_ids), callback_query=call)


def audio_update(file_id: str, caption: str | None = None, user: User = OWNER) -> Update:
    audio = Audio(file_id=file_id, file_unique_id=file_id, duration=100, file_name="rec.mp3", file_size=5000)
    return message_update(user, audio=audio, caption=caption)


# ---------- новые пользователи и вход ----------

async def test_new_user_is_asked_to_connect_yandex(env):
    await env.dp.feed_update(env.bot, message_update(NEWBIE, text="/start"))
    assert "Привет, Новичок" in env.tg.texts()[-1]
    assert MenuCb(action="login").pack() in [b.callback_data for b in env.tg.buttons()]

    await env.dp.feed_update(env.bot, message_update(NEWBIE, text="кино"))
    doc = Document(file_id="S1", file_unique_id="S1", file_name="x.mp3", mime_type="audio/mpeg", file_size=10)
    await env.dp.feed_update(env.bot, message_update(NEWBIE, document=doc))
    assert all("Сначала подключите Яндекс Музыку" in t for t in env.tg.texts()[1:])
    assert not env.ym.searches and not env.ym.uploads, "чужой аккаунт не используется"


async def test_login_via_bot(env):
    await env.dp.feed_update(env.bot, message_update(NEWBIE, text="/login"))
    text = env.tg.texts()[-1]
    assert "<code>ABCD1234</code>" in text and "ya.ru/device" in text
    assert env.tg.buttons()[0].url == "https://ya.ru/device"

    for _ in range(100):  # вход подтверждается в фоне
        if any("подключена" in t for t in env.tg.texts()):
            break
        await asyncio.sleep(0.02)
    assert "✅ <b>Яндекс Музыка подключена!</b>" in env.tg.texts()[-1] and "newbie" in env.tg.texts()[-1]
    assert env.store.get_account(NEWBIE.id) == ("tok-newbie", "newbie")

    await env.dp.feed_update(env.bot, message_update(NEWBIE, text="кино"))
    assert env.newbie.searches == ["кино"]


async def test_logout_via_bot(env):
    await env.dp.feed_update(env.bot, message_update(FRIEND, text="/logout"))
    assert "Отключить аккаунт <b>friend</b>" in env.tg.texts()[-1]
    await env.dp.feed_update(env.bot, callback_update(MenuCb(action="logout_ok").pack(), FRIEND))
    assert "Аккаунт отключён" in env.tg.texts()[-1]
    assert not env.accounts.is_connected(FRIEND.id)
    assert env.accounts.is_connected(OWNER.id)


async def test_users_work_with_their_own_accounts(env):
    await env.dp.feed_update(env.bot, message_update(OWNER, text="кино"))
    await env.dp.feed_update(env.bot, message_update(FRIEND, text="земфира"))
    assert env.ym.searches == ["кино"] and env.friend.searches == ["земфира"]

    env.store.set_upload_target(OWNER.id, 1003)
    doc = Document(file_id="D9", file_unique_id="D9", file_name="demo.mp3", mime_type="audio/mpeg", file_size=10)
    await env.dp.feed_update(env.bot, message_update(FRIEND, document=doc))
    assert env.friend.uploads == [] and env.ym.uploads == [], "цель загрузки владельца не касается друга"
    assert "В какой плейлист" in env.tg.texts()[-1]


async def test_start_shows_account(env):
    await env.dp.feed_update(env.bot, message_update(text="/start"))
    assert "🎧 <b>me</b> · Плюс ✅" in env.tg.texts()[0]
    assert MenuCb(action="likes").pack() in [b.callback_data for b in env.tg.buttons()]


async def test_broken_account_is_explained_instead_of_silence(env):
    env.ym.start_error = "Яндекс Музыка не приняла вход"
    await env.dp.feed_update(env.bot, message_update(text="/start"))
    await env.dp.feed_update(env.bot, message_update(text="кино"))
    texts = env.tg.texts()
    assert len(texts) == 2 and all("Не получается подключиться" in t and "не приняла вход" in t for t in texts)
    assert env.tg.buttons()[0].callback_data == MenuCb(action="login").pack()


async def test_revoked_login_is_explained(env):
    from yandex_music.exceptions import UnauthorizedError

    async def revoked(query, type_):
        raise UnauthorizedError("401")

    env.ym.search = revoked
    await env.dp.feed_update(env.bot, message_update(text="кино"))
    assert "не приняла ваш вход" in env.tg.texts()[-1] and "/login" in env.tg.texts()[-1]
    assert OWNER.id not in env.accounts._clients, "клиент сброшен — при следующем запросе подключимся заново"


# ---------- поиск, загрузка, скачивание ----------

async def test_search_shows_download_buttons(env):
    await env.dp.feed_update(env.bot, message_update(text="кино кукушка"))
    buttons = env.tg.buttons()
    assert any(b.callback_data == "t:dl:123" and "Кино — Кукушка" in b.text for b in buttons)


async def settle() -> None:
    """Дать доработать фоновым задачам (отчёт о переносе в начало плейлиста)."""
    for _ in range(5):
        await asyncio.sleep(0)


async def test_upload_asks_playlist_then_uploads_all_files(env):
    await env.dp.feed_update(env.bot, audio_update("F1", caption="Кино - Кукушка"))
    await env.dp.feed_update(env.bot, audio_update("F2"))

    prompts = [c for c in env.tg.calls if isinstance(c, SendMessage)]
    assert len(prompts) == 1, "на несколько файлов — одна карточка"
    card = env.tg.texts()[-1]
    assert "Готово к загрузке: 2" in card and "1. Кино — Кукушка ✏️" in card and "2. rec" in card
    labels = [b.text for b in env.tg.buttons()]
    assert "✏️ 1. Кино — Кукушка" in labels and any(t.startswith("📤 Мои записи") for t in labels)
    assert env.ym.uploads == []

    await env.dp.feed_update(env.bot, callback_update(UploadCb(action="to", kind=1003).pack()))
    await settle()

    assert [(k, n) for k, n, _ in env.ym.uploads] == [(1003, "Кино - Кукушка.mp3"), (1003, "rec.mp3")]
    assert read_mp3_tags(env.ym.uploads[0][2]) == ("Кино", "Кукушка")
    assert [(uid, kind) for uid, kind, _ in env.placer.placed] == [(42, 1003), (42, 1003)]
    status = env.tg.texts()[-1]
    assert "✅ Загружено в «Мои записи»: 2 из 2" in status and "треки уже в начале плейлиста" in status


async def test_default_playlist_is_one_tap_away(env):
    env.store.set_upload_target(OWNER.id, 1003)
    doc = Document(file_id="D1", file_unique_id="D1", file_name="demo.mp3", mime_type="audio/mpeg", file_size=10)
    await env.dp.feed_update(env.bot, message_update(document=doc))
    assert env.ym.uploads == [], "сначала карточка — можно поправить данные"
    upload = env.tg.button("⬆️ Загрузить в «Мои записи»")
    assert env.tg.button("✏️ Изменить данные трека")

    await env.dp.feed_update(env.bot, callback_update(upload.callback_data))
    assert [(k, n) for k, n, _ in env.ym.uploads] == [(1003, "demo.mp3")]


async def test_track_editor_changes_tags_and_cover(env):
    await env.dp.feed_update(env.bot, audio_update("F7"))
    await env.dp.feed_update(env.bot, callback_update(env.tg.button("✏️ Изменить").callback_data))
    editor = env.tg.texts()[-1]
    assert "✏️ <b>Данные трека</b>" in editor and "🎵 Название: <b>rec</b>" in editor and "Обложка: нет" in editor
    pid = EditCb.unpack(env.tg.button("🎵").callback_data).pid

    async def set_field(action: str, value: str) -> None:
        await env.dp.feed_update(env.bot, callback_update(EditCb(action=action, pid=pid).pack()))
        await env.dp.feed_update(env.bot, message_update(text=value))

    await set_field("title", "Новая песня")
    await set_field("artist", "Мы")
    await set_field("year", "в прошлом году")
    assert "Год — четыре цифры" in env.tg.texts()[-1], "неправильный год — просим ещё раз"
    await env.dp.feed_update(env.bot, message_update(text="2023"))
    await set_field("album", "Демо")
    editor = env.tg.texts()[-1]
    assert "🎵 Название: <b>Новая песня</b> ✏️" in editor and "📅 Год: <b>2023</b> ✏️" in editor
    assert any(isinstance(c, DeleteMessage) for c in env.tg.calls), "вопрос и ответ убираются из чата"

    await env.dp.feed_update(env.bot, callback_update(EditCb(action="cover", pid=pid).pack()))
    photo = [PhotoSize(file_id="PH-small", file_unique_id="s", width=90, height=90),
             PhotoSize(file_id="PH-big", file_unique_id="b", width=800, height=800)]
    await env.dp.feed_update(env.bot, message_update(photo=photo))
    shown = [c for c in env.tg.calls if isinstance(c, SendPhoto)][-1]
    assert shown.photo.data == FAKE_JPEG and "Обложка: новая ✏️" in shown.caption, "редактор показывает новую обложку"

    await env.dp.feed_update(env.bot, callback_update(EditCb(action="done", pid=pid).pack()))
    assert "1. Мы — Новая песня ✏️" in env.tg.texts()[-1]
    await env.dp.feed_update(env.bot, callback_update(env.tg.button("📤 Мои записи").callback_data))
    [(kind, name, data)] = env.ym.uploads
    meta = read_tags(data)
    assert name == "Мы - Новая песня.mp3"
    assert (meta.title, meta.artist, meta.album, meta.year) == ("Новая песня", "Мы", "Демо", "2023")
    assert meta.cover == FAKE_JPEG


async def test_editor_can_remove_cover_and_reset_title(env):
    tagged = tag_mp3(FAKE_MP3, artist="Автор", title="Из файла", cover=FAKE_JPEG)

    async def stream(url, **kwargs):
        yield tagged

    env.tg.stream_content = stream
    await env.dp.feed_update(env.bot, audio_update("F8", caption="Подпись - Из подписи"))
    await env.dp.feed_update(env.bot, callback_update(env.tg.button("✏️ Изменить").callback_data))
    shown = [c for c in env.tg.calls if isinstance(c, SendPhoto)][-1]
    assert "Обложка: из файла" in shown.caption and "<b>Из подписи</b> ✏️" in shown.caption
    pid = EditCb.unpack(env.tg.button("🗑").callback_data).pid

    await env.dp.feed_update(env.bot, callback_update(EditCb(action="nocover", pid=pid).pack()))
    await env.dp.feed_update(env.bot, callback_update(EditCb(action="title", pid=pid).pack()))
    await env.dp.feed_update(env.bot, message_update(text="-"))
    assert "🎵 Название: <b>Из файла</b>\n" in env.tg.texts()[-1] and "без обложки" in env.tg.texts()[-1]

    await env.dp.feed_update(env.bot, callback_update(env.tg.button("✅").callback_data))
    await env.dp.feed_update(env.bot, callback_update(env.tg.button("📤 Мои записи").callback_data))
    meta = read_tags(env.ym.uploads[0][2])
    assert (meta.title, meta.artist, meta.cover) == ("Из файла", "Подпись", None)


async def test_non_audio_document_is_not_uploaded(env):
    doc = Document(file_id="D2", file_unique_id="D2", file_name="notes.pdf", mime_type="application/pdf")
    await env.dp.feed_update(env.bot, message_update(document=doc))
    assert env.ym.uploads == []
    assert "Не понял" in env.tg.texts()[-1]


async def test_download_track_sends_tagged_mp3_and_caches_file_id_per_account(env):
    def click(user: User) -> Update:
        return callback_update(TrackCb(action="dl", track="123").pack(), user)

    await env.dp.feed_update(env.bot, click(OWNER))
    first = [c for c in env.tg.calls if isinstance(c, SendAudio)][0]
    assert first.title == "Кукушка" and first.performer == "Кино" and first.duration == 400
    assert first.audio.filename == "Кино - Кукушка.mp3"
    assert read_mp3_tags(first.audio.data) == ("Кино", "Кукушка")
    assert "Звезда по имени Солнце, 1989" in first.caption

    await env.dp.feed_update(env.bot, click(OWNER))
    second = [c for c in env.tg.calls if isinstance(c, SendAudio)][1]
    assert second.audio == "AUDIO-1", "повторно трек отправляется по file_id без скачивания"
    assert env.ym.downloads == 1

    await env.dp.feed_update(env.bot, click(FRIEND))
    assert env.friend.downloads == 1, "у другого аккаунта (может быть без Плюса) — свой файл"


async def test_app_command_without_webapp_url(env):
    await env.dp.feed_update(env.bot, message_update(text="/app"))
    assert "WEBAPP_URL" in env.tg.texts()[-1]


def test_app_button_opens_webapp():
    from bot.keyboards import app_button, main_menu

    assert app_button("https://music.example.com").web_app.url == "https://music.example.com"
    assert app_button(None) is None
    first_row = main_menu("https://music.example.com").inline_keyboard[0]
    assert first_row[0].web_app.url == "https://music.example.com"
    assert len(main_menu(None).inline_keyboard[0]) == 2, "без мини-приложения меню начинается с кнопок списков"


# ---------- запуск ----------

class _TelegramRejects(FakeTelegram):
    def __init__(self, error) -> None:
        super().__init__()
        self.error = error

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        raise self.error(method=method, message="Unauthorized")


async def test_startup_explains_bad_bot_token():
    from aiogram.exceptions import TelegramUnauthorizedError

    from bot.main import check_telegram

    bot = Bot("42:WRONG", session=_TelegramRejects(TelegramUnauthorizedError))
    with pytest.raises(SystemExit, match="Telegram отклонил BOT_TOKEN"):
        await check_telegram(bot)


async def test_startup_removes_stale_webhook():
    from aiogram.methods import DeleteWebhook, GetMe

    from bot.main import check_telegram

    class Ok(FakeTelegram):
        async def make_request(self, bot, method, timeout=None):
            self.calls.append(method)
            if isinstance(method, GetMe):
                return User(id=42, is_bot=True, first_name="Bot", username="my_music_bot")
            return True

    session = Ok()
    me = await check_telegram(Bot("42:OK", session=session))
    assert me.username == "my_music_bot"
    assert any(isinstance(c, DeleteWebhook) for c in session.calls), "старый webhook мешает polling"


async def test_description_is_set_only_if_empty():
    from aiogram.methods import GetMyDescription, GetMyShortDescription, SetMyDescription, SetMyShortDescription
    from aiogram.types import BotDescription, BotShortDescription

    from bot.main import DESCRIPTION, setup_description

    class Session(FakeTelegram):
        async def make_request(self, bot, method, timeout=None):
            self.calls.append(method)
            if isinstance(method, GetMyDescription):
                return BotDescription(description="")
            if isinstance(method, GetMyShortDescription):
                return BotShortDescription(short_description="Моё описание")
            return True

    session = Session()
    await setup_description(Bot("42:OK", session=session))
    [desc] = [c for c in session.calls if isinstance(c, SetMyDescription)]
    assert desc.description == DESCRIPTION
    assert not any(isinstance(c, SetMyShortDescription) for c in session.calls), "своё описание владельца не трогаем"


async def test_settings_change_download_quality(env):
    await env.dp.feed_update(env.bot, message_update(text="/settings"))
    text = env.tg.texts()[-1]
    assert "⚙️ <b>Настройки</b>" in text and "<b>320 kbps</b>" in text and "<b>me</b>" in text
    assert [b.text for b in env.tg.buttons()][:4] == ["✅ 320", "192", "128", "64"]

    await env.dp.feed_update(env.bot, callback_update(SettingsCb(kind="download", value=128).pack()))
    assert "<b>128 kbps</b>" in env.tg.texts()[-1]
    assert env.store.get_setting(OWNER.id, "download_quality") == "128"

    def click() -> Update:
        return callback_update(TrackCb(action="dl", track="123").pack())

    await env.dp.feed_update(env.bot, click())
    await env.dp.feed_update(env.bot, click())
    assert env.ym.bitrates == [128], "качество из настроек; повтор — из кэша"

    await env.dp.feed_update(env.bot, callback_update(SettingsCb(kind="download", value=320).pack()))
    await env.dp.feed_update(env.bot, click())
    assert env.ym.bitrates == [128, 320], "в другом качестве — другой файл"

    await env.dp.feed_update(env.bot, callback_update(SettingsCb(kind="download", value=999).pack()))
    assert env.store.get_setting(OWNER.id, "download_quality") == "320", "чужие значения не принимаем"


# ---------- защита ----------

async def test_errors_do_not_reveal_details(env):
    async def broken(query, type_):
        raise RuntimeError("/srv/bot/secret.py: что-то внутри")

    env.ym.search = broken
    await env.dp.feed_update(env.bot, message_update(text="кино"))
    text = env.tg.texts()[-1]
    assert "Что-то пошло не так" in text and "код" in text
    assert "secret" not in text and "RuntimeError" not in text


async def test_failed_telegram_download_does_not_leak_bot_token(env):
    """В тексте ошибки aiohttp — адрес api.telegram.org/file/bot<токен>/…, его нельзя показывать в чате."""
    import aiohttp
    from multidict import CIMultiDict, CIMultiDictProxy
    from yarl import URL

    url = URL("https://api.telegram.org/file/bot42:TEST/music/file_1.mp3")
    info = aiohttp.RequestInfo(url, "GET", CIMultiDictProxy(CIMultiDict()), url)
    error = aiohttp.ClientResponseError(info, (), status=404, message="Not Found")
    assert "42:TEST" in str(error)

    async def failing(*args, **kwargs):
        raise error
        yield b""  # noqa: RET503 — это асинхронный генератор

    env.tg.stream_content = failing
    await env.dp.feed_update(env.bot, audio_update("F1"))
    await env.dp.feed_update(env.bot, callback_update(UploadCb(action="to", kind=1003).pack()))
    await settle()
    status = env.tg.texts()[-1]
    assert "Не загрузилось: 1" in status and "HTTP 404" in status
    assert all("42:TEST" not in t for t in env.tg.texts())


async def test_upload_queue_is_limited(env, monkeypatch):
    monkeypatch.setattr("bot.handlers.upload.MAX_QUEUE", 2)
    for file_id in ("F1", "F2", "F3"):
        await env.dp.feed_update(env.bot, audio_update(file_id))
    assert "В очереди уже 2 файлов" in env.tg.texts()[-1]
    assert [f.file_id for f in env.dp.workflow_data["uploads"].pending[OWNER.id]] == ["F1", "F2"]


# ---------- инлайн-режим ----------

def inline_update(query: str, user: User = OWNER, offset: str = "") -> Update:
    iq = InlineQuery(id=str(next(_update_ids)), from_user=user, query=query, offset=offset)
    return Update(update_id=next(_update_ids), inline_query=iq)


def chosen_update(result_id: str, user: User = OWNER) -> Update:
    chosen = ChosenInlineResult(result_id=result_id, from_user=user, query="кино", inline_message_id="IM1")
    return Update(update_id=next(_update_ids), chosen_inline_result=chosen)


def inline_answers(env) -> list[AnswerInlineQuery]:
    return [c for c in env.tg.calls if isinstance(c, AnswerInlineQuery)]


async def test_inline_search_then_send_track(env):
    await env.dp.feed_update(env.bot, inline_update("кино"))
    [answer] = inline_answers(env)
    assert env.ym.searches == ["кино"] and answer.is_personal
    [result] = answer.results
    assert result.type == "article" and result.id == "t:123" and result.title == "Кукушка"
    assert "Загружаю" in result.input_message_content.message_text
    assert result.reply_markup.inline_keyboard[0][0].url == "https://t.me/testbot?start=t123"

    # Выбрали трек: бот качает его, кладёт в Telegram (через свой чат, без звука) и подменяет сообщение аудио.
    await env.dp.feed_update(env.bot, chosen_update("t:123"))
    [upload] = [c for c in env.tg.calls if isinstance(c, SendAudio)]
    assert upload.chat_id == OWNER.id and upload.disable_notification
    assert any(isinstance(c, DeleteMessage) for c in env.tg.calls), "служебное сообщение удалено"
    [edit] = [c for c in env.tg.calls if isinstance(c, EditMessageMedia)]
    assert edit.inline_message_id == "IM1" and edit.media.media == "AUDIO-1"

    # Второй раз — из кэша, мгновенно.
    env.tg.calls.clear()
    await env.dp.feed_update(env.bot, inline_update("кино"))
    [result] = inline_answers(env)[0].results
    assert result.type == "audio" and result.audio_file_id == "AUDIO-1"


async def test_inline_empty_query_shows_likes(env):
    await env.dp.feed_update(env.bot, inline_update(""))
    [answer] = inline_answers(env)
    assert [r.id for r in answer.results] == ["t:123"] and env.ym.searches == []
    assert "Мне нравится" in answer.button.text


async def test_inline_without_yandex_offers_login(env):
    await env.dp.feed_update(env.bot, inline_update("кино", NEWBIE))
    [answer] = inline_answers(env)
    assert answer.results == [] and answer.button.start_parameter == "login"


async def test_start_links_from_inline(env):
    await env.dp.feed_update(env.bot, message_update(text="/start t123"))
    assert any(isinstance(c, SendAudio) for c in env.tg.calls), "трек из кнопки «Слушать в боте»"
    await env.dp.feed_update(env.bot, message_update(NEWBIE, text="/start login"))
    assert "<code>ABCD1234</code>" in env.tg.texts()[-1]


# ---------- админка в чате ----------

async def test_admin_commands_only_for_admin(env):
    await env.dp.feed_update(env.bot, message_update(FRIEND, text="/admin"))
    assert "Не понял" in env.tg.texts()[-1], "для остальных команды нет"
    await env.dp.feed_update(env.bot, message_update(FRIEND, text="/ban 1"))
    assert "Не понял" in env.tg.texts()[-1]
    assert not env.store.is_banned(OWNER.id)

    await env.dp.feed_update(env.bot, message_update(text="/admin"))
    assert "🛡 <b>Админ-панель</b>" in env.tg.texts()[-1] and "Пользователей: <b>2</b>" in env.tg.texts()[-1]


async def test_admin_bans_user(env):
    await env.dp.feed_update(env.bot, message_update(FRIEND, text="кино"))
    await env.dp.feed_update(env.bot, message_update(text=f"/ban {FRIEND.id} спам"))
    assert "заблокирован" in env.tg.texts()[-1]
    await env.dp.feed_update(env.bot, message_update(FRIEND, text="кино"))
    assert "Доступ к боту для вас закрыт" in env.tg.texts()[-1]
    assert env.friend.searches == ["кино"], "после блокировки бот его запросы не выполняет"
    await env.dp.feed_update(env.bot, message_update(text=f"/ban {OWNER.id}"))
    assert "Админа заблокировать нельзя" in env.tg.texts()[-1]

    await env.dp.feed_update(env.bot, message_update(text=f"/user {FRIEND.id}"))
    assert "⛔ <b>Заблокирован</b>: спам" in env.tg.texts()[-1]
    from bot.callbacks import AdminCb
    await env.dp.feed_update(env.bot, callback_update(AdminCb(action="unban", user=FRIEND.id).pack()))
    assert not env.store.is_banned(FRIEND.id)
    assert [e["action"] for e in env.store.audit()] == ["unban", "ban"]


async def test_admin_maintenance_mode(env):
    from bot.callbacks import AdminCb
    await env.dp.feed_update(env.bot, callback_update(AdminCb(action="maintenance").pack()))
    await env.dp.feed_update(env.bot, message_update(FRIEND, text="кино"))
    assert "техническом обслуживании" in env.tg.texts()[-1] and env.friend.searches == []
    await env.dp.feed_update(env.bot, message_update(text="кино"))
    assert env.ym.searches == ["кино"], "админ пользуется ботом и во время техработ"
    await env.dp.feed_update(env.bot, callback_update(AdminCb(action="maintenance").pack(), FRIEND))
    assert env.dp["admin"].settings.maintenance, "кнопки админки у других не работают"


async def test_download_limit(env):
    env.dp["admin"].update_settings(OWNER.id, {"download_limit": 1})
    await env.dp.feed_update(env.bot, callback_update(TrackCb(action="dl", track="123").pack(), FRIEND))
    await env.dp.feed_update(env.bot, callback_update(TrackCb(action="dl", track="123").pack(), FRIEND))
    assert "Дневной лимит скачиваний (1) исчерпан" in env.tg.texts()[-1]
    assert len([c for c in env.tg.calls if isinstance(c, SendAudio)]) == 1


async def test_admin_broadcast_from_chat(env):
    from bot.callbacks import AdminCb
    await env.dp.feed_update(env.bot, message_update(FRIEND, text="/start"))
    await env.dp.feed_update(env.bot, message_update(text="/broadcast"))
    await env.dp.feed_update(env.bot, message_update(text="Новая версия бота!"))
    assert env.tg.texts()[-2] == "Новая версия бота!" and "Кому отправить" in env.tg.texts()[-1]
    await env.dp.feed_update(env.bot, callback_update(AdminCb(action="bc_all").pack()))
    admin = env.dp["admin"]
    for _ in range(100):
        if not admin.broadcaster.running:
            break
        await asyncio.sleep(0.02)
    got = [c.chat_id for c in env.tg.calls if isinstance(c, SendMessage) and c.text == "Новая версия бота!"]
    assert sorted(got) == [OWNER.id, OWNER.id, FRIEND.id], "предпросмотр админу + рассылка обоим"
    assert admin.broadcaster.status["state"] == "done" and "Рассылка завершена" in env.tg.texts()[-1]


async def test_admin_backup_goes_to_admin_chat(env, tmp_path):
    from bot.callbacks import AdminCb
    await env.dp.feed_update(env.bot, callback_update(AdminCb(action="backup").pack()))
    [doc] = [c for c in env.tg.calls if isinstance(c, SendDocument)]
    assert doc.chat_id == OWNER.id and "никому не пересылайте" in doc.caption
    assert not list(tmp_path.glob("backup-*")), "временная копия удалена"


async def test_user_blocking_the_bot_is_noted(env):
    from aiogram.types import ChatMemberBanned, ChatMemberMember, ChatMemberUpdated

    chat = Chat(id=FRIEND.id, type="private")
    me = User(id=42, is_bot=True, first_name="Bot")
    update = ChatMemberUpdated(chat=chat, from_user=FRIEND, date=datetime.now(),
                               old_chat_member=ChatMemberMember(user=me),
                               new_chat_member=ChatMemberBanned(user=me, until_date=0))
    await env.dp.feed_update(env.bot, Update(update_id=next(_update_ids), my_chat_member=update))
    assert env.store.get_user(FRIEND.id)["blocked_bot"]
    await env.dp.feed_update(env.bot, message_update(FRIEND, text="/start"))
    assert not env.store.get_user(FRIEND.id)["blocked_bot"], "вернулся — снова получает рассылки"
