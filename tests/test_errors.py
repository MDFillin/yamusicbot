"""Ошибки: ожидаемые — короткой строкой, неожиданные (баги) — с трассировкой; падения фоновых задач не теряются."""

import asyncio
import logging

import aiohttp

from bot.errors import is_expected, log_failure, spawn
from bot.ym import UploadError

log = logging.getLogger("test.errors")


def test_expected_errors_are_short_warnings(caplog):
    with caplog.at_level(logging.WARNING):
        log_failure(log, "Трек %s не скачался", 1, exc=aiohttp.ClientConnectionError("нет сети"))
        log_failure(log, "Загрузка", exc=UploadError("Яндекс отклонил файл"))
    assert [r.levelname for r in caplog.records] == ["WARNING", "WARNING"]
    assert all(r.exc_info is None for r in caplog.records)
    assert "нет сети" in caplog.records[0].getMessage()


def test_unexpected_errors_keep_traceback(caplog):
    try:
        {}["нет"]
    except KeyError as e:
        error = e
    assert not is_expected(error)
    with caplog.at_level(logging.WARNING):
        log_failure(log, "Трек %s не скачался", 1, exc=error)
    [record] = caplog.records
    assert record.levelname == "ERROR" and record.exc_info[1] is error, "баг виден в админке с трассировкой"


async def test_spawned_task_failure_is_logged(caplog):
    keep: set = set()

    async def broken():
        raise RuntimeError("упало в фоне")

    with caplog.at_level(logging.ERROR):
        task = spawn(broken(), "проверка", keep)
        assert task in keep
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)
    assert not keep, "ссылка на задачу освобождена"
    [record] = [r for r in caplog.records if "проверка" in r.getMessage()]
    assert record.levelname == "ERROR" and "упало в фоне" in str(record.exc_info[1])


async def test_cancelled_task_is_quiet(caplog):
    with caplog.at_level(logging.ERROR):
        task = spawn(asyncio.sleep(10), "долгая")
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)
    assert not caplog.records
