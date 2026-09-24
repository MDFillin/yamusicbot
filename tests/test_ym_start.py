"""Подключение к Яндексу не роняет бота: ошибка объясняется и попытка повторяется позже."""

import pytest
from yandex_music.exceptions import NetworkError, UnauthorizedError

from bot import ym as ym_module
from bot.ym import YandexMusic, YandexNotReady, explain_start_error


def test_explain_start_error():
    assert "Подключите аккаунт заново" in explain_start_error(UnauthorizedError("Unknown HTTPError"))
    assert "сервера бота" in explain_start_error(UnauthorizedError("x"))
    assert "api.music.yandex.net" in explain_start_error(NetworkError("timeout"))
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
