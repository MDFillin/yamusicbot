"""Админ-панель мини-приложения: доступ только владельцу, блокировки, техработы, лимиты, рассылка."""

from __future__ import annotations

import asyncio
import logging
import time

import pytest
from aiogram import Bot
from aiogram.methods import SendDocument, SendMessage
from aiohttp.test_utils import TestClient, TestServer
from test_bot_flow import FakeTelegram
from test_web import (
    FRIEND_ID,
    NEWBIE_ID,
    OWNER_ID,
    TOKEN,
    FakeOAuth,
    FakePlacer,
    FakeSender,
    FakeYM,
    as_user,
    init_data,
)

from bot.accounts import Accounts
from bot.admin import Admin
from bot.config import Config
from bot.storage import Storage
from bot.web.app import create_app

ADMIN_ID = OWNER_ID


@pytest.fixture
async def env(tmp_path, upstream):
    fakes = {"tok-owner": FakeYM(upstream), "tok-friend": FakeYM(upstream, uid=77, login="friend", likes=["2:10"])}
    store = Storage(tmp_path / "bot.db")
    store.set_account(OWNER_ID, "tok-owner", "me")
    store.set_account(FRIEND_ID, "tok-friend", "friend")
    accounts = Accounts(store, factory=fakes.__getitem__, oauth=FakeOAuth())
    config = Config(bot_token=TOKEN, data_dir=tmp_path, admin_ids=frozenset({ADMIN_ID}))
    telegram = FakeTelegram()
    bot = Bot(TOKEN, session=telegram)
    admin = Admin(config, store, accounts, bot)
    client = TestClient(TestServer(create_app(config, bot, accounts, store, FakeSender(), FakePlacer(), admin)))
    await client.start_server()
    client.session.headers["X-Telegram-Init-Data"] = init_data(ADMIN_ID)
    yield type("Env", (), {"client": client, "store": store, "admin": admin, "tg": telegram})
    await client.close()
    await accounts.close()
    store.close()


# ---------- кто может войти ----------

async def test_admin_api_is_invisible_to_others(env):
    c = env.client
    for path in ("/api/admin/overview", "/api/admin/users", "/api/admin/nope"):
        r = await c.get(path, headers=as_user(FRIEND_ID))
        assert r.status == 404, path
    r = await c.put("/api/admin/settings", json={"maintenance": True}, headers=as_user(FRIEND_ID))
    assert r.status == 404 and not env.admin.settings.maintenance
    r = await c.post(f"/api/admin/users/{FRIEND_ID}/ban", json={}, headers=as_user(FRIEND_ID))
    assert r.status == 404 and not env.store.is_banned(FRIEND_ID)

    [entry] = [e for e in env.store.audit() if e["action"] == "denied"]
    assert entry["actor"] == FRIEND_ID, "попытка записана в журнал (один раз, без спама)"
    alerts = [m for m in env.tg.calls if isinstance(m, SendMessage) and "🚨" in m.text]
    assert len(alerts) == 1 and alerts[0].chat_id == ADMIN_ID, "владельцу пришло предупреждение"
    me = await (await c.get("/api/me", headers=as_user(FRIEND_ID))).json()
    assert "is_admin" not in me


async def test_admin_needs_valid_and_fresh_signature(env):
    c = env.client
    forged = init_data(ADMIN_ID, token="1:OTHER")
    assert (await c.get("/api/admin/overview", headers={"X-Telegram-Init-Data": forged})).status == 401
    assert (await c.get("/api/admin/overview", headers={"X-Telegram-Init-Data": ""})).status == 401
    stale = init_data(ADMIN_ID, auth_date=int(time.time()) - 2 * 3600)
    r = await c.get("/api/admin/overview", headers={"X-Telegram-Init-Data": stale})
    assert r.status == 401 and (await r.json())["code"] == "stale_session"
    r = await c.get("/api/admin/overview")
    assert r.status == 200 and r.headers["Cache-Control"] == "no-store"
    assert (await (await c.get("/api/me")).json())["is_admin"] is True


async def test_overview(env):
    await env.client.get("/api/library", headers=as_user(FRIEND_ID))
    data = await (await env.client.get("/api/admin/overview")).json()
    assert data["stats"]["users"]["total"] == 2 and data["stats"]["users"]["connected"] == 2
    assert data["stats"]["users"]["active_today"] == 2 and len(data["stats"]["series"]) == 14
    assert data["server"]["admins"] == [ADMIN_ID] and data["settings"]["maintenance"] is False
    assert data["broadcast"]["state"] == "idle"


# ---------- пользователи ----------

async def test_users_search_and_ban(env):
    c = env.client
    await c.get("/api/library", headers=as_user(FRIEND_ID))
    users = (await (await c.get("/api/admin/users?q=friend")).json())["users"]
    assert [u["id"] for u in users] == [FRIEND_ID] and users[0]["yandex_login"] == "friend"

    r = await c.post(f"/api/admin/users/{FRIEND_ID}/ban", json={"reason": "спам"})
    assert r.status == 200 and (await r.json())["banned"]
    r = await c.get("/api/library", headers=as_user(FRIEND_ID))
    assert r.status == 403 and (await r.json())["code"] == "banned"
    banned = (await (await c.get("/api/admin/users?status=banned")).json())["users"]
    assert [u["ban_reason"] for u in banned] == ["спам"]

    r = await c.post(f"/api/admin/users/{ADMIN_ID}/ban", json={})
    assert r.status == 400 and "Админа" in (await r.json())["error"]

    await c.post(f"/api/admin/users/{FRIEND_ID}/unban")
    assert (await c.get("/api/library", headers=as_user(FRIEND_ID))).status == 200


async def test_banned_user_media_links_stop_working(env):
    c = env.client
    track = (await (await c.get("/api/source/likes", headers=as_user(FRIEND_ID))).json())["tracks"][0]
    assert (await c.get(track["stream"])).status == 200
    await c.post(f"/api/admin/users/{FRIEND_ID}/ban", json={})
    assert (await c.get(track["stream"])).status == 403


async def test_disconnect_and_message(env):
    c = env.client
    r = await c.post(f"/api/admin/users/{FRIEND_ID}/disconnect")
    assert r.status == 200 and not (await r.json())["connected"]
    assert env.store.get_account(FRIEND_ID) is None
    r = await c.post(f"/api/admin/users/{FRIEND_ID}/message", json={"text": "Привет от админа"})
    assert r.status == 200
    assert any(isinstance(m, SendMessage) and m.chat_id == FRIEND_ID and m.text == "Привет от админа"
               for m in env.tg.calls)
    assert [e["action"] for e in env.store.audit()][:2] == ["message", "disconnect"]


# ---------- режимы и лимиты ----------

async def test_maintenance_and_closed_registration(env):
    c = env.client
    r = await c.put("/api/admin/settings", json={"maintenance": True, "maintenance_text": "Обновляемся до 18:00"})
    assert (await r.json())["maintenance"]
    r = await c.get("/api/me", headers=as_user(FRIEND_ID))
    assert r.status == 403 and (await r.json())["error"] == "🛠 Обновляемся до 18:00"
    assert (await c.get("/api/library")).status == 200, "админ работает и во время техработ"

    await c.put("/api/admin/settings", json={"maintenance": False, "closed": True})
    assert (await c.get("/api/me", headers=as_user(FRIEND_ID))).status == 200, "старые пользователи остаются"
    r = await c.get("/api/me", headers=as_user(NEWBIE_ID))
    assert r.status == 403 and (await r.json())["code"] == "closed"
    await c.put("/api/admin/settings", json={"closed": False})
    assert (await c.get("/api/me", headers=as_user(NEWBIE_ID))).status == 200


async def test_limits(env):
    c = env.client
    r = await c.put("/api/admin/settings", json={"download_limit": 1})
    assert (await r.json())["download_limit"] == 1
    assert (await c.put("/api/admin/settings", json={"download_limit": -5})).status == 400
    track = (await (await c.get("/api/source/likes", headers=as_user(FRIEND_ID))).json())["tracks"][0]
    assert (await c.get(track["download"])).status == 200
    r = await c.get(track["download"])
    assert r.status == 429 and "лимит" in (await r.json())["error"]
    user = await (await c.get(f"/api/admin/users/{FRIEND_ID}")).json()
    assert user["downloads"] == 1 and user["downloads_left"] == 0
    await c.post(f"/api/admin/users/{FRIEND_ID}/reset")
    assert (await c.get(track["download"])).status == 200


# ---------- рассылка, журнал, ошибки, бэкап ----------

async def test_broadcast(env):
    c = env.client
    await c.get("/api/me", headers=as_user(FRIEND_ID))
    await c.post(f"/api/admin/users/{NEWBIE_ID}/ban", json={})
    r = await c.post("/api/admin/broadcast", json={"text": "<b>Новость</b>", "test": True})
    assert r.status == 200
    r = await c.post("/api/admin/broadcast", json={"text": "<b>Новость</b>", "audience": "all"})
    body = await r.json()
    assert r.status == 200 and body["total"] == 2, "заблокированным рассылка не уходит"
    for _ in range(100):
        if not env.admin.broadcaster.running:
            break
        await asyncio.sleep(0.02)
    status = await (await c.get("/api/admin/broadcast")).json()
    assert status["state"] == "done" and status["sent"] == 2
    got = sorted(m.chat_id for m in env.tg.calls if isinstance(m, SendMessage) and m.text == "<b>Новость</b>")
    assert got == [ADMIN_ID, ADMIN_ID, FRIEND_ID], "проверка себе + всем"
    assert (await c.post("/api/admin/broadcast", json={"text": " "})).status == 400


async def test_errors_audit_and_backup(env, tmp_path):
    logging.getLogger().addHandler(env.admin.errors)
    try:
        logging.getLogger("bot.test").error("Ошибка на /api/x [c0ffee]")
    finally:
        logging.getLogger().removeHandler(env.admin.errors)
    entries = (await (await env.client.get("/api/admin/errors?q=c0ffee")).json())["entries"]
    assert len(entries) == 1 and entries[0]["level"] == "ERROR"

    r = await env.client.post("/api/admin/backup")
    assert r.status == 200 and (await r.json())["size"] > 0
    [doc] = [m for m in env.tg.calls if isinstance(m, SendDocument)]
    assert doc.chat_id == ADMIN_ID
    assert not list(tmp_path.glob("backup-*"))
    audit = (await (await env.client.get("/api/admin/audit")).json())["entries"]
    assert audit[0]["action"] == "backup" and audit[0]["label"] == "бэкап базы"
