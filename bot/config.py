"""Настройки бота из переменных окружения (или файла .env)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

FXTUNNEL_DOMAIN_RE = re.compile(r"[a-z0-9][a-z0-9-]{1,30}[a-z0-9]")


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Config:
    bot_token: str
    data_dir: Path
    bot_api_url: str | None = None
    bot_api_local: bool = False
    telegram_proxy: str | None = None  # http://… или socks5://… — если сервер не видит api.telegram.org
    max_bitrate: int = 320
    # Мини-приложение: публичный HTTPS-адрес (для кнопки в Telegram) и где слушать веб-сервер.
    webapp_url: str | None = None
    web_host: str = "0.0.0.0"
    web_port: int = 8080
    web_max_upload_mb: int = 300
    # Прежняя однопользовательская настройка: токен YANDEX_MUSIC_TOKEN один раз привязывается к ALLOWED_USERS.
    legacy_ym_token: str | None = None
    legacy_users: frozenset[int] = frozenset()
    # Telegram ID владельцев: только им доступны /admin и админ-панель в мини-приложении.
    admin_ids: frozenset[int] = frozenset()
    # Ключ шифрования входов в базе; без него ключ хранится в data/secret.key (см. bot/crypto.py).
    encryption_key: str | None = None

    @property
    def max_tg_download(self) -> int:
        """Сколько байт бот может скачать из Telegram (облачный Bot API режет на 20 МБ)."""
        return 2000 * 1024 * 1024 if self.bot_api_url else 20 * 1024 * 1024

    @property
    def max_tg_upload(self) -> int:
        """Сколько байт бот может отправить в Telegram (облачный Bot API режет на 50 МБ)."""
        return 2000 * 1024 * 1024 if self.bot_api_url else 50 * 1024 * 1024


def _parse_users(raw: str) -> frozenset[int]:
    """Список Telegram ID через запятую (ADMIN_IDS, прежняя ALLOWED_USERS)."""
    parts = (p.strip() for p in raw.replace(";", ",").split(","))
    return frozenset(int(p) for p in parts if p.lstrip("-").isdigit())


def _flag(raw: str | None) -> bool:
    return (raw or "").strip().lower() in {"1", "true", "yes", "on"}


def load_config() -> Config:
    load_dotenv()

    bot_token = os.getenv("BOT_TOKEN", "").strip()
    if not bot_token:
        raise ConfigError("Не задан BOT_TOKEN (токен бота от @BotFather)")

    bitrate = int(os.getenv("MAX_BITRATE", "320"))
    if bitrate not in (64, 128, 192, 320):
        raise ConfigError("MAX_BITRATE должен быть одним из: 64, 128, 192, 320")

    webapp_url = os.getenv("WEBAPP_URL", "").strip().rstrip("/") or None
    fxtunnel_domain = os.getenv("FXTUNNEL_DOMAIN", "").strip().lower() or None
    if fxtunnel_domain and not FXTUNNEL_DOMAIN_RE.fullmatch(fxtunnel_domain):
        raise ConfigError("FXTUNNEL_DOMAIN: 3–32 символа — латиница, цифры и дефис (не в начале и не в конце)")
    if not webapp_url and fxtunnel_domain:
        webapp_url = f"https://{fxtunnel_domain}.fxtun.ru"  # адрес, который выдаёт туннель fxTunnel (домен fxtun.ru)
    if webapp_url and not webapp_url.startswith("https://"):
        raise ConfigError("WEBAPP_URL должен начинаться с https:// — мини-приложения Telegram работают только по HTTPS")

    telegram_proxy = os.getenv("TELEGRAM_PROXY", "").strip() or None
    if telegram_proxy and not telegram_proxy.startswith(("http://", "socks4://", "socks5://")):
        raise ConfigError("TELEGRAM_PROXY должен начинаться с http://, socks5:// или socks4://")

    return Config(
        bot_token=bot_token,
        data_dir=Path(os.getenv("DATA_DIR", "data")),
        bot_api_url=os.getenv("BOT_API_URL", "").strip() or None,
        bot_api_local=_flag(os.getenv("BOT_API_LOCAL")),
        telegram_proxy=telegram_proxy,
        max_bitrate=bitrate,
        webapp_url=webapp_url,
        web_host=os.getenv("WEB_HOST", "0.0.0.0"),
        web_port=int(os.getenv("WEB_PORT", "8080")),
        web_max_upload_mb=int(os.getenv("WEB_MAX_UPLOAD_MB", "300")),
        legacy_ym_token=os.getenv("YANDEX_MUSIC_TOKEN", "").strip() or None,
        legacy_users=_parse_users(os.getenv("ALLOWED_USERS", "")),
        admin_ids=_admin_ids(os.getenv("ADMIN_IDS", "")),
        encryption_key=os.getenv("ENCRYPTION_KEY", "").strip() or None,
    )


def _admin_ids(raw: str) -> frozenset[int]:
    ids = _parse_users(raw)
    junk = [p.strip() for p in raw.replace(";", ",").split(",") if p.strip() and not p.strip().isdigit()]
    if junk:  # опечатка здесь тихо оставила бы бота без админа — лучше сказать сразу
        raise ConfigError(f"ADMIN_IDS: ожидались числовые Telegram ID через запятую, а не {', '.join(junk)}")
    return frozenset(i for i in ids if i > 0)
