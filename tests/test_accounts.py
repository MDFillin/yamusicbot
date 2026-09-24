"""Аккаунты пользователей: база, вход по коду Яндекса, выход и перенос прежнего общего токена."""

from __future__ import annotations

import asyncio
import json
import stat
from types import SimpleNamespace as NS

import pytest
from yandex_music.exceptions import DeviceAuthError, NetworkError, UnauthorizedError

from bot import accounts as accounts_module
from bot.accounts import Accounts, LoginError
from bot.storage import Storage
from bot.ym import YandexNotReady


class FakeYM:
    """Клиент Яндекса: токен «bad» Яндекс не принимает."""

    def __init__(self, token: str) -> None:
        self.token = token
        self.uid = None
        self.login = None
        self.has_plus = True
        self.closed = False

    @property
    def ready(self) -> bool:
        return self.uid is not None

    async def start(self) -> None:
        if self.token == "bad":
            raise UnauthorizedError("401")
        self.uid, self.login = 42, f"login-{self.token}"

    async def ensure_started(self) -> None:
        try:
            await self.start()
        except UnauthorizedError as e:
            raise YandexNotReady("вход устарел") from e

    async def close(self) -> None:
        self.closed = True


class FakeOAuth:
    """Страница Яндекса: подтверждение приходит на `confirm_after`-й опрос (или ошибка из `error`)."""

    def __init__(self, token: str = "tok-new", confirm_after: int = 2, error: str | None = None,
                 expires_in: int = 300) -> None:
        self.token, self.confirm_after, self.error, self.expires_in = token, confirm_after, error, expires_in
        self.codes = 0
        self.polls = 0

    async def request_device_code(self, device_name=None):
        self.codes += 1
        return NS(device_code=f"dev-{self.codes}", user_code=f"CODE{self.codes}", verification_url="https://ya.ru/device",
                  expires_in=self.expires_in, interval=0)

    async def poll_device_token(self, device_code):
        self.polls += 1
        if self.polls == 1:
            raise NetworkError("сеть моргнула")  # временная ошибка не прерывает вход
        if self.error:
            raise DeviceAuthError(self.error)
        return NS(access_token=self.token) if self.polls >= self.confirm_after else None


@pytest.fixture(autouse=True)
def fast_polling(monkeypatch):
    monkeypatch.setattr(accounts_module, "MIN_POLL_INTERVAL", 0.01)
    monkeypatch.setattr(accounts_module, "MIN_LOGIN_INTERVAL", 0)


@pytest.fixture
def store(tmp_path):
    s = Storage(tmp_path / "bot.db")
    yield s
    s.close()


def make_accounts(store: Storage, oauth=None) -> Accounts:
    return Accounts(store, factory=FakeYM, oauth=oauth or FakeOAuth())


# ---------- база ----------

def test_storage_keeps_data_between_restarts(tmp_path):
    path = tmp_path / "bot.db"
    s = Storage(path)
    s.set_account(1, "tok", "me")
    s.set_upload_target(1, 1003)
    s.set_file_id(42, "123", 320, "FILE")
    s.close()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600, "в базе токены — читать её может только владелец"

    s = Storage(path)
    assert s.get_account(1) == ("tok", "me")
    assert s.get_upload_target(1) == 1003
    assert s.get_file_id(42, "123", 320) == "FILE"
    assert s.get_file_id(7, "123", 320) is None, "кэш file_id — на аккаунт Яндекса"
    assert s.delete_account(1) and s.get_account(1) is None
    assert s.get_upload_target(1) is None, "с аккаунтом забываются и его настройки"
    s.close()


def test_settings_are_imported_from_old_json_once(tmp_path):
    (tmp_path / "storage.json").write_text(json.dumps({
        "file_ids": {"1@320": "OLD"}, "upload_targets": {"5": 1003},
    }), "utf-8")
    s = Storage(tmp_path / "bot.db")
    assert s.get_upload_target(5) == 1003
    s.set_upload_target(5, None)
    s.close()
    s = Storage(tmp_path / "bot.db")
    assert s.get_upload_target(5) is None, "повторно не импортируем"
    s.close()


# ---------- подключённые аккаунты ----------

async def test_clients_are_per_user(store):
    store.set_account(1, "one", None)
    store.set_account(2, "two", None)
    accounts = make_accounts(store)
    a, b = await accounts.get(1), await accounts.get(2)
    assert (a.token, b.token) == ("one", "two")
    assert await accounts.get(1) is a, "клиент создаётся один раз"
    assert await accounts.get(3) is None
    assert store.get_account(1) == ("one", "login-one"), "логин запоминается после подключения"


async def test_broken_login_is_reported(store):
    store.set_account(1, "bad", None)
    accounts = make_accounts(store)
    with pytest.raises(YandexNotReady):
        await accounts.get(1)
    assert accounts.is_connected(1), "аккаунт не удаляем сами: 403 бывает и из-за блокировки по IP"


async def test_logout_forgets_token(store):
    store.set_account(1, "one", None)
    accounts = make_accounts(store)
    ym = await accounts.get(1)
    assert await accounts.logout(1)
    assert ym.closed and not accounts.is_connected(1) and await accounts.get(1) is None
    assert not await accounts.logout(1)


def test_legacy_token_goes_to_old_owners_once(store):
    accounts = make_accounts(store)
    store.set_account(2, "own", "x")
    assert accounts.import_legacy_token("legacy", frozenset({1, 2})) == [1]
    assert store.get_account(1) == ("legacy", None)
    assert store.get_account(2) == ("own", "x"), "свой вход важнее старого общего токена"

    store.delete_account(1)
    assert accounts.import_legacy_token("legacy", frozenset({1, 2})) == [], "после выхода токен сам не возвращается"
    assert accounts.import_legacy_token(None, frozenset({1})) == []
    assert accounts.import_legacy_token("x", frozenset()) == []


# ---------- вход по коду ----------

async def test_login_by_code(store):
    oauth = FakeOAuth(confirm_after=3)
    accounts = make_accounts(store, oauth)
    session = await accounts.start_login(1)
    assert (session.code, session.url, session.status) == ("CODE1", "https://ya.ru/device", "pending")
    assert await accounts.start_login(1) is session, "бот и приложение показывают один и тот же код"

    ym = await asyncio.wait_for(session.wait(), 2)
    assert ym.login == "login-tok-new" and session.status == "done"
    assert store.get_account(1) == ("tok-new", "login-tok-new")
    assert await accounts.get(1) is ym
    assert oauth.polls == 3

    session2 = await accounts.start_login(1)
    assert session2 is not session and session2.code == "CODE2", "после входа — новый код"
    accounts.cancel_login(1)


async def test_login_replaces_previous_account(store):
    store.set_account(1, "old", "old-login")
    accounts = make_accounts(store)
    old = await accounts.get(1)
    session = await accounts.start_login(1)
    new = await asyncio.wait_for(session.wait(), 2)
    assert old.closed and await accounts.get(1) is new and store.get_account(1)[0] == "tok-new"


@pytest.mark.parametrize(("oauth", "message"), [
    (FakeOAuth(error="access_denied"), "отклонён"),
    (FakeOAuth(error="expired_token"), "истёк"),
    (FakeOAuth(confirm_after=10**6, expires_in=0), "истёк"),
    (FakeOAuth(token="bad"), "не приняла вход"),
])
async def test_login_failures_are_explained(store, oauth, message):
    accounts = make_accounts(store, oauth)
    session = await accounts.start_login(1)
    with pytest.raises(LoginError, match=message):
        await asyncio.wait_for(session.wait(), 2)
    assert session.status == "failed" and message in session.error
    assert not accounts.is_connected(1)


async def test_login_can_be_cancelled(store):
    accounts = make_accounts(store, FakeOAuth(confirm_after=10**6))
    session = await accounts.start_login(1)
    accounts.cancel_login(1)
    with pytest.raises(asyncio.CancelledError):
        await session.wait()
    assert session.status == "cancelled" and accounts.login_session(1) is None


async def test_login_codes_are_not_requested_too_often(store, monkeypatch):
    monkeypatch.setattr(accounts_module, "MIN_LOGIN_INTERVAL", 60)
    accounts = make_accounts(store, FakeOAuth(confirm_after=10**6))
    await accounts.start_login(1)
    accounts.cancel_login(1)
    with pytest.raises(LoginError, match="Подождите"):
        await accounts.start_login(1)
    assert (await accounts.start_login(2)).code == "CODE2", "у других пользователей свой счётчик"
    await accounts.close()
