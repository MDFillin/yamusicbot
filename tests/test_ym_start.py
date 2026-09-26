"""Подключение к Яндексу не роняет бота: ошибка объясняется и попытка повторяется позже."""

import pytest
from yandex_music.exceptions import NetworkError, TimedOutError, UnauthorizedError

from bot import ym as ym_module
from bot.ym import YandexMusic, YandexNotReady, explain_start_error


def test_explain_start_error():
    assert "Подключите аккаунт заново" in explain_start_error(UnauthorizedError("Unknown HTTPError"))
    assert "сервера бота" in explain_start_error(UnauthorizedError("x"))
    for e in (NetworkError("timeout"), TimedOutError(), TimeoutError(), ConnectionResetError()):
        assert "не может связаться" in explain_start_error(e) and "Входить заново не нужно" in explain_start_error(e)
    assert "ValueError" in explain_start_error(ValueError("boom"))


async def test_ensure_started_retries_with_pause(monkeypatch):
    ym = YandexMusic("bad")
    calls = []

    async def failing_start():
        calls.append(1)
        raise UnauthorizedError("Unknown HTTPError")

    monkeypatch.setattr(ym, "start", failing_start)
    with pytest.raises(YandexNotReady, match="заново"):
        await ym.ensure_started()
    with pytest.raises(YandexNotReady):
        await ym.ensure_started()
    assert len(calls) == 1, "повторная попытка не раньше чем через START_RETRY_INTERVAL"

    monkeypatch.setattr(ym_module, "START_RETRY_INTERVAL", 0)

    async def ok_start():
        calls.append(2)
        ym._client = object()

    monkeypatch.setattr(ym, "start", ok_start)
    await ym.ensure_started()
    assert ym.ready and ym.start_error is None and calls == [1, 2]
    await ym.ensure_started()
    assert calls == [1, 2], "после успеха больше не подключаемся"


async def test_unreachable_yandex_is_marked_as_network(monkeypatch):
    ym = YandexMusic("tok")

    async def timed_out():
        raise TimedOutError()

    monkeypatch.setattr(ym, "start", timed_out)
    with pytest.raises(YandexNotReady) as first:
        await ym.ensure_started()
    with pytest.raises(YandexNotReady) as cached:
        await ym.ensure_started()
    assert first.value.network and cached.value.network, "дело в связи, а не во входе — и при повторе из кэша"

    monkeypatch.setattr(ym_module, "START_RETRY_INTERVAL", 0)

    async def revoked():
        raise UnauthorizedError("401")

    monkeypatch.setattr(ym, "start", revoked)
    with pytest.raises(YandexNotReady) as bad_login:
        await ym.ensure_started()
    assert not bad_login.value.network
