"""scripts/backup.sh и scripts/restore.sh: копия → Telegram → восстановление на чистом сервере."""

import asyncio
import os
import shutil
import sqlite3
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(not shutil.which("openssl") or not shutil.which("bash"), reason="нужны bash и openssl")


@pytest.fixture
async def telegram(tmp_path):
    """Поддельный Bot API: принимает документ, отдаёт его по file_id."""
    store = {"files": {}, "sent": [], "messages": []}

    async def send_document(request):
        form = await request.post()
        doc = form["document"]
        data = doc.file.read()
        store["files"]["F1"] = data
        store["sent"].append((request.match_info["token"], form["chat_id"], doc.filename, len(data)))
        return web.json_response({"ok": True, "result": {"document": {"file_id": "F1", "file_name": doc.filename}}})

    async def send_message(request):
        form = await request.post()
        store["messages"].append((form["chat_id"], form["text"]))
        return web.json_response({"ok": True, "result": {}})

    async def get_file(request):
        if request.query.get("file_id") not in store["files"]:
            return web.json_response({"ok": False, "description": "Bad Request: invalid file_id"}, status=400)
        return web.json_response({"ok": True, "result": {"file_path": "documents/file_1.enc"}})

    async def download(request):
        return web.Response(body=store["files"]["F1"])

    app = web.Application(client_max_size=100 * 1024 * 1024)
    app.router.add_post("/bot{token}/sendDocument", send_document)
    app.router.add_post("/bot{token}/sendMessage", send_message)
    app.router.add_get("/bot{token}/getFile", get_file)
    app.router.add_get("/file/bot{token}/documents/{name}", download)
    server = TestServer(app)
    await server.start_server()
    store["url"] = str(server.make_url("")).rstrip("/")
    yield store
    await server.close()


def bot_dir(path: Path) -> Path:
    """Папка бота, как на сервере: скрипты, .env, база, ключ туннеля; docker — заглушка, пишущая свои вызовы."""
    (path / "scripts").mkdir(parents=True)
    for name in ("backup.sh", "restore.sh"):
        shutil.copy(ROOT / "scripts" / name, path / "scripts" / name)
    bin_dir = path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text('#!/bin/sh\necho "$@" >> "$DOCKER_LOG"\ncase "$*" in *"ps --status"*) echo bot;; esac\n')
    docker.chmod(0o755)
    return path


async def run(cwd: Path, script: str, *args: str, stdin: str = "", **env: str) -> tuple[int, str]:
    full_env = {**os.environ, "PATH": f"{cwd / 'bin'}:{os.environ['PATH']}", "DOCKER_LOG": str(cwd / "docker.log"),
                "HOME": str(cwd), **env}
    proc = await asyncio.create_subprocess_exec(
        "bash", f"scripts/{script}", *args, cwd=cwd, env=full_env, stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    out, _ = await proc.communicate(stdin.encode())
    return proc.returncode, out.decode()


async def test_backup_and_restore_via_telegram(tmp_path, telegram):
    old = bot_dir(tmp_path / "old")
    (old / ".env").write_text('BOT_TOKEN=123:ABC\nADMIN_IDS="42,43"\nENCRYPTION_KEY=k\n')
    (old / "data").mkdir()
    db = sqlite3.connect(old / "data" / "bot.db")
    db.execute("CREATE TABLE accounts (user_id INTEGER, token TEXT)")
    db.execute("INSERT INTO accounts VALUES (42, 'зашифрованный-токен')")
    db.commit()
    db.close()
    (old / "ssh").mkdir()
    (old / "ssh" / "id_ed25519").write_text("KEY")

    code, out = await run(old, "backup.sh", BACKUP_PASSWORD="пароль 123", TG_API=telegram["url"])
    assert code == 0, out
    assert "✅ Копия готова" in out and "✅ Копия у вас в Telegram" in out and "restore.sh F1" in out
    [(token, chat, filename, size)] = telegram["sent"]
    assert (token, chat) == ("123:ABC", "42") and filename.startswith("yamusicbot-backup-") and size > 0
    [(chat, text)] = telegram["messages"]
    assert chat == "42" and "bash scripts/restore.sh F1" in text, "команда восстановления остаётся в чате"
    archive = next(old.glob("yamusicbot-backup-*.tar.gz.enc"))
    assert oct(archive.stat().st_mode & 0o777) == "0o600", "в архиве токены — только владельцу"
    assert b"ABC" not in archive.read_bytes(), "архив зашифрован"
    log = (old / "docker.log").read_text()
    assert "compose stop bot" in log and "compose start bot" in log, "бот стоит только на время копирования"

    new = bot_dir(tmp_path / "new")  # чистый сервер: только склонированный бот
    code, out = await run(new, "restore.sh", "F1", BOT_TOKEN="123:ABC", BACKUP_PASSWORD="пароль 123",
                          TG_API=telegram["url"])
    assert code == 0, out
    assert (new / ".env").read_text() == (old / ".env").read_text()
    assert (new / "ssh" / "id_ed25519").read_text() == "KEY"
    rows = sqlite3.connect(new / "data" / "bot.db").execute("SELECT * FROM accounts").fetchall()
    assert rows == [(42, "зашифрованный-токен")]
    assert "compose up -d --build" in (new / "docker.log").read_text() and "✅ Бот запущен" in out


async def test_restore_refuses_wrong_password_and_keeps_files(tmp_path, telegram):
    old = bot_dir(tmp_path / "old")
    (old / ".env").write_text("BOT_TOKEN=1:X\n")  # без ADMIN_IDS — в Telegram не отправляем
    (old / "data").mkdir()
    sqlite3.connect(old / "data" / "bot.db").close()
    code, out = await run(old, "backup.sh", BACKUP_PASSWORD="верный", TG_API=telegram["url"])
    assert code == 0 and "scp root@" in out and not telegram["sent"]
    archive = next(old.glob("yamusicbot-backup-*.tar.gz.enc"))

    new = bot_dir(tmp_path / "new")
    (new / ".env").write_text("BOT_TOKEN=NEW\n")
    code, out = await run(new, "restore.sh", str(archive), stdin="n\n", BACKUP_PASSWORD="верный")
    assert code == 1 and "Отменено" in out and (new / ".env").read_text() == "BOT_TOKEN=NEW\n"
    code, out = await run(new, "restore.sh", str(archive), RESTORE_FORCE="1", BACKUP_PASSWORD="неверный")
    assert code == 1 and "неверный пароль" in out and (new / ".env").read_text() == "BOT_TOKEN=NEW\n"
    code, out = await run(new, "restore.sh", str(archive), RESTORE_FORCE="1", BACKUP_PASSWORD="верный",
                          RESTORE_NO_START="1")
    assert code == 0 and (new / ".env").read_text() == "BOT_TOKEN=1:X\n"


async def test_backup_asks_password_twice(tmp_path, telegram):
    old = bot_dir(tmp_path / "old")
    (old / ".env").write_text("BOT_TOKEN=1:X\n")
    (old / "data").mkdir()
    sqlite3.connect(old / "data" / "bot.db").close()
    code, out = await run(old, "backup.sh", stdin="раз\nдва\nпароль\nпароль\n", TG_API=telegram["url"])
    assert code == 0 and "Не совпало" in out and "✅ Копия готова" in out
    new = bot_dir(tmp_path / "new")
    archive = next(old.glob("yamusicbot-backup-*.tar.gz.enc"))
    code, _ = await run(new, "restore.sh", str(archive), BACKUP_PASSWORD="пароль", RESTORE_NO_START="1")
    assert code == 0 and (new / "data" / "bot.db").exists()
