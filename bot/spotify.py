"""Spotify Web API для переноса музыки: вход по OAuth с PKCE и нужные переносу запросы.

С 2026 года приложение Spotify в режиме разработки обслуживает только своего владельца (с Premium) и до пяти
добавленных им людей. Поэтому общего приложения у бота нет: каждый пользователь регистрирует своё на
developer.spotify.com и вставляет его Client ID в «Медиатеку». PKCE не требует секрета приложения — Client ID
не секретный, его можно показывать и хранить открыто; секретны только токены (хранятся зашифрованными).

Эндпоинты — уже по правилам февраля 2026 (dev mode): /me/playlists, /playlists/{id}/items, /me/library,
поиск не больше 10 результатов за запрос.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import re
import secrets
import time
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import aiohttp

from bot import net

log = logging.getLogger(__name__)

AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API = "https://api.spotify.com/v1"
SCOPES = " ".join((
    "user-library-read", "user-library-modify", "user-follow-modify",
    "playlist-read-private", "playlist-read-collaborative", "playlist-modify-private", "playlist-modify-public",
))
CLIENT_ID_RE = re.compile(r"[0-9a-f]{32}")
LIBRARY_BATCH = 40  # PUT /me/library — не больше 40 URI
PLAYLIST_BATCH = 100  # POST /playlists/{id}/items — не больше 100
SEARCH_LIMIT = 10  # в режиме разработки больше 10 за запрос не отдают
MIN_INTERVAL = 0.25  # сек между запросами одного пользователя: у каждого своё приложение и своя квота
MAX_RETRY_AFTER = 120  # сек: дольше не ждём — перенос сообщит об ошибке, а не повиснет


class SpotifyError(Exception):
    def __init__(self, text: str, status: int | None = None) -> None:
        super().__init__(text)
        self.status = status


class SpotifyAuthError(SpotifyError):
    """Вход недействителен (отозван, удалён пользователь из приложения): нужно войти заново."""


def pkce_pair() -> tuple[str, str]:
    """(verifier, challenge) по RFC 7636."""
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def authorize_url(client_id: str, redirect_uri: str, state: str, challenge: str) -> str:
    return AUTH_URL + "?" + urlencode({
        "client_id": client_id, "response_type": "code", "redirect_uri": redirect_uri, "state": state,
        "scope": SCOPES, "code_challenge_method": "S256", "code_challenge": challenge,
    })


@dataclass
class Tokens:
    access: str
    refresh: str
    expires_at: float

    def to_dict(self) -> dict[str, Any]:
        return {"access": self.access, "refresh": self.refresh, "expires_at": self.expires_at}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Tokens:
        return cls(str(data["access"]), str(data["refresh"]), float(data["expires_at"]))


def _tokens(data: dict[str, Any], old_refresh: str | None = None) -> Tokens:
    if not data.get("access_token"):
        raise SpotifyAuthError("Spotify не выдал доступ")
    return Tokens(data["access_token"], data.get("refresh_token") or old_refresh or "",
                  time.time() + int(data.get("expires_in") or 3600) - 60)


async def _token_request(http: aiohttp.ClientSession, form: dict[str, str]) -> dict[str, Any]:
    async with http.post(TOKEN_URL, data=form, timeout=net.timeout(30)) as resp:
        try:
            data = await resp.json(content_type=None)
        except ValueError:
            data = {}
        if resp.status != 200:
            error = (data or {}).get("error", "")
            text = (data or {}).get("error_description") or error or f"HTTP {resp.status}"
            if error in ("invalid_grant", "invalid_client", "unauthorized_client"):
                raise SpotifyAuthError(f"Spotify отклонил вход: {text}", resp.status)
            raise SpotifyError(f"Spotify: {text}", resp.status)
        return data


async def exchange_code(http: aiohttp.ClientSession, client_id: str, redirect_uri: str, code: str,
                        verifier: str) -> Tokens:
    return _tokens(await _token_request(http, {
        "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
        "client_id": client_id, "code_verifier": verifier,
    }))


class Spotify:
    """Клиент одного пользователя. on_tokens вызывается, когда Spotify выдал новые токены (их надо сохранить)."""

    def __init__(self, http: aiohttp.ClientSession, client_id: str, tokens: Tokens, on_tokens=None) -> None:
        self.http = http
        self.client_id = client_id
        self.tokens = tokens
        self.on_tokens = on_tokens
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def _access(self) -> str:
        if time.time() >= self.tokens.expires_at:
            data = await _token_request(self.http, {"grant_type": "refresh_token", "refresh_token": self.tokens.refresh,
                                                    "client_id": self.client_id})
            self.tokens = _tokens(data, self.tokens.refresh)
            if self.on_tokens:
                self.on_tokens(self.tokens)
        return self.tokens.access

    async def request(self, method: str, path: str, *, params: dict | None = None, json: Any = None) -> Any:
        url = path if path.startswith("https://") else API + path
        for attempt in range(4):
            async with self._lock:  # запросы одного человека — по очереди и не чаще MIN_INTERVAL
                wait = self._last + MIN_INTERVAL - time.monotonic()
                if wait > 0:
                    await asyncio.sleep(wait)
                self._last = time.monotonic()
                headers = {"Authorization": f"Bearer {await self._access()}"}
                try:
                    async with self.http.request(method, url, params=params, json=json, headers=headers,
                                                 timeout=net.timeout(30)) as resp:
                        if resp.status == 429:
                            retry = int(resp.headers.get("Retry-After", "5") or 5)
                            if retry > MAX_RETRY_AFTER:
                                raise SpotifyError(f"Spotify просит подождать {retry // 60} мин — слишком много "
                                                   "запросов. Попробуйте позже", 429)
                            log.info("Spotify 429: жду %s с", retry)
                            self._last = time.monotonic() + retry
                            continue
                        if resp.status == 401 and attempt == 0:
                            self.tokens.expires_at = 0  # истёк раньше срока — обновим и повторим
                            continue
                        if resp.status >= 500 and attempt < 3:
                            self._last = time.monotonic() + 2 * (attempt + 1)
                            continue
                        if resp.status in (200, 201, 202, 204):
                            try:
                                return await resp.json(content_type=None)  # пустое тело — None
                            except ValueError:
                                return None
                        text = await resp.text()
                        message = _error_text(text) or f"HTTP {resp.status}"
                        if resp.status in (401, 403):
                            raise SpotifyAuthError(_auth_hint(resp.status, message), resp.status)
                        raise SpotifyError(f"Spotify: {message}", resp.status)
                except (aiohttp.ClientError, TimeoutError) as e:
                    if attempt == 3:
                        raise SpotifyError(f"Нет связи со Spotify: {e.__class__.__name__}") from e
                    self._last = time.monotonic() + 2
        raise SpotifyError("Spotify не ответил после нескольких попыток")

    async def _pages(self, path: str, limit: int = 50) -> AsyncIterator[dict[str, Any]]:
        url: str | None = path
        params: dict | None = {"limit": limit}
        while url:
            page = await self.request("GET", url, params=params) or {}
            for item in page.get("items") or []:
                yield item
            url, params = page.get("next"), None

    # ---------- чтение ----------

    async def me(self) -> dict[str, Any]:
        return await self.request("GET", "/me") or {}

    async def playlists(self) -> list[dict[str, Any]]:
        return [p async for p in self._pages("/me/playlists") if p]

    async def search_tracks(self, query: str) -> list[dict[str, Any]]:
        data = await self.request("GET", "/search", params={"q": query, "type": "track", "limit": SEARCH_LIMIT})
        return [t for t in ((data or {}).get("tracks") or {}).get("items") or [] if t]

    # ---------- запись ----------

    async def save_tracks(self, uris: Sequence[str]) -> None:
        """В «Любимые треки». Spotify ставит дату добавления по порядку запросов — сохраняем от старых к новым."""
        for i in range(0, len(uris), LIBRARY_BATCH):
            await self.request("PUT", "/me/library", params={"uris": ",".join(uris[i:i + LIBRARY_BATCH])})

    async def create_playlist(self, name: str, description: str = "") -> dict[str, Any]:
        return await self.request("POST", "/me/playlists",
                                  json={"name": name[:100], "public": False, "description": description[:300]})

    async def add_to_playlist(self, playlist_id: str, uris: Sequence[str]) -> None:
        for i in range(0, len(uris), PLAYLIST_BATCH):
            batch = list(uris[i:i + PLAYLIST_BATCH])
            await self.request("POST", f"/playlists/{playlist_id}/items", json={"uris": batch})


def _error_text(text: str) -> str:
    try:
        data = json.loads(text)
    except ValueError:
        return text[:200]
    err = data.get("error") if isinstance(data, dict) else None
    if isinstance(err, dict):
        return str(err.get("message") or "")[:200]
    return str(data.get("error_description") or err or "")[:200] if isinstance(data, dict) else ""


def _auth_hint(status: int, message: str) -> str:
    if status == 403 and "user may not be registered" in message.lower():
        return ("Spotify не пускает этот аккаунт в ваше приложение: войдите тем аккаунтом, на который "
                "зарегистрировано приложение, или добавьте его email в User Management приложения")
    if status == 403 and "premium" in message.lower():
        return "Spotify требует Premium у владельца приложения — без него приложение не работает"
    if status == 401:
        return "Вход в Spotify устарел — подключите Spotify заново"
    return f"Spotify запретил действие: {message}"
