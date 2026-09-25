"""Ynison — через него приложения Яндекс Музыки (телефон, компьютер, сайт, колонки) синхронизируют плеер.

Бот подключается к Ynison аккаунта как пассивное устройство: не плеер и не пульт, ничего не меняет и не
перехватывает, только получает состояние плеера при каждом изменении — смена трека, пауза, перемотка, повтор.
По этим кадрам bot/live.py считает каждое прослушивание, включая повторы, и сколько на самом деле слушали.

Протокол неофициальный: JSON-представление protobuf, как у официальных клиентов (сверено с модулем ynison
библиотеки yandex-music, 2026). Имена полей бывают в snake_case и camelCase, 64-битные числа — строками,
перечисления — именами или числами; разбор ниже терпим ко всему этому.
"""

from __future__ import annotations

import json
import re
import urllib.parse
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import aiohttp

YNISON_URL = "wss://ynison.music.yandex.ru"
STATE_SCHEME = "wss"  # в тестах — ws: локальный сервер без TLS
REDIRECT_PATH = "/redirector.YnisonRedirectService/GetRedirectToYnison"
STATE_PATH = "/ynison_state.YnisonStateService/PutYnisonState"
ORIGIN = "https://music.yandex.ru"
HANDSHAKE_TIMEOUT = 20
DEFAULT_KEEPALIVE = 20
MAX_FRAME = 32 * 1024 * 1024  # в кадре вся очередь: у длинного плейлиста это сотни килобайт
# Токен уходит только на хосты Яндекса, даже если редиректор вдруг назовёт другой.
_HOST_RE = re.compile(r"^[a-z0-9.-]+\.(yandex\.(ru|net|com)|ya\.ru)(:\d{1,5})?$")
_TRACK_ID_RE = re.compile(r"^[\w.:-]{1,64}$")

ENTITY_TYPES = {1: "ARTIST", 2: "PLAYLIST", 3: "ALBUM", 4: "RADIO", 5: "VARIOUS", 6: "GENERATIVE", 7: "FM_RADIO",
                8: "VIDEO_WAVE", 9: "LOCAL_TRACKS"}
PLAYABLE_TRACK = ("TRACK", 1, "1")


class YnisonAuthError(Exception):
    """Яндекс не принял токен: вход отозван или истёк."""


class YnisonProtocolError(Exception):
    """Ответ не такой, как ожидали: скорее всего, Яндекс изменил протокол."""


def _get(obj: Any, name: str, default: Any = None) -> Any:
    """Поле по имени в snake_case, если нет — в camelCase."""
    if not isinstance(obj, dict):
        return default
    if name in obj:
        return obj[name]
    head, *rest = name.split("_")
    return obj.get(head + "".join(p.title() for p in rest), default)


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float(value: Any, default: float = 1.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if 0 < result <= 4 else default


@dataclass(frozen=True)
class Snapshot:
    """Что играет в момент кадра. track_id None — ничего (пустая очередь, клип, локальный файл)."""

    track_id: str | None
    album_id: str | None
    context: str
    context_type: str
    progress_ms: int  # позиция на момент status_ts
    duration_ms: int
    paused: bool
    speed: float
    status_ts: int  # мс, когда устройство сообщило эту позицию (по часам устройства!)
    frame_ts: int  # мс, время сервера в момент кадра
    status_key: str = ""  # версия статуса: меняется, когда устройство сообщает новую позицию или паузу

    def clamp(self, pos: int) -> int:
        return max(min(pos, self.duration_ms) if self.duration_ms else pos, 0)

    def position(self) -> int:
        """Позиция на момент кадра: если играет, со времени status_ts трек успел продвинуться.

        Часы устройства могут расходиться с сервером, поэтому так считается только первый кадр после
        подключения — дальше bot/live.py отсчитывает время сам, от получения нового статуса.
        """
        pos = self.progress_ms
        if not self.paused and self.status_ts and self.frame_ts > self.status_ts:
            pos += int((self.frame_ts - self.status_ts) * self.speed)
        return self.clamp(pos)

    def stale(self) -> bool:
        """«Играет», но статус старше самого трека: устройство пропало, не сказав «пауза»."""
        if self.paused or not self.status_ts or not self.frame_ts:
            return False
        return self.frame_ts - self.status_ts > (self.duration_ms or 1_800_000) + 60_000


def queue_context(queue: dict) -> tuple[str, str]:
    """Откуда играет очередь — в тех же ключах, что источники из истории Яндекса (bot/listening.py)."""
    raw_type = _get(queue, "entity_type")
    entity_type = ENTITY_TYPES.get(raw_type, raw_type) if isinstance(raw_type, int) else str(raw_type or "")
    entity_id = str(_get(queue, "entity_id") or "")
    if entity_type == "RADIO" or _get(_get(queue, "queue"), "wave_queue") is not None:
        return ("wave:" + entity_id if entity_id else "wave:user:onyourwave"), "wave"
    if not entity_id:
        return "none", "other"
    if entity_type == "PLAYLIST":
        return "playlist:" + entity_id, "playlist"  # «uid:kind» — как в истории
    if entity_type == "ALBUM":
        return "album:" + entity_id, "album"
    if entity_type == "ARTIST":
        return "artist:" + entity_id, "artist"
    return "none", "other"


def parse_state(frame: Any) -> Snapshot | None:
    """Состояние плеера из кадра. None — в кадре нет состояния плеера (служебный кадр).

    YnisonAuthError / YnisonProtocolError — сервер ответил ошибкой или формат не тот, что ожидаем.
    """
    if not isinstance(frame, dict):
        raise YnisonProtocolError(f"кадр не объект: {str(frame)[:200]}")
    error = frame.get("error")
    if error:
        text = json.dumps(error, ensure_ascii=False)[:300]
        if any(w in text.lower() for w in ("unauthorized", "unauthenticated", "401", "403", "forbidden")):
            raise YnisonAuthError(text)
        raise YnisonProtocolError(f"ошибка от сервера: {text}")
    player = _get(frame, "player_state")
    if player is None:
        return None
    queue, status = _get(player, "player_queue"), _get(player, "status")
    if not isinstance(queue, dict) or not isinstance(status, dict):
        raise YnisonProtocolError(f"нет очереди или статуса: {json.dumps(player, ensure_ascii=False)[:300]}")
    playables = _get(queue, "playable_list") or []
    if not isinstance(playables, list):
        raise YnisonProtocolError("playable_list не список")
    index = _int(_get(queue, "current_playable_index"), -1)
    track_id = album_id = None
    if 0 <= index < len(playables) and isinstance(playables[index], dict):
        item = playables[index]
        kind = _get(item, "playable_type", "TRACK")
        playable_id = str(_get(item, "playable_id") or "")
        if kind in PLAYABLE_TRACK and _TRACK_ID_RE.match(playable_id):
            track_id = playable_id.split(":")[0]  # «трек:альбом» → трек
            album_id = str(_get(item, "album_id_optional") or "") or None
    context, context_type = queue_context(queue)
    version = _get(status, "version") or {}
    status_key = ":".join(str(_get(version, k) or "") for k in ("device_id", "version", "timestamp_ms"))
    return Snapshot(
        track_id=track_id, album_id=album_id, context=context, context_type=context_type,
        progress_ms=max(_int(_get(status, "progress_ms")), 0), duration_ms=max(_int(_get(status, "duration_ms")), 0),
        paused=bool(_get(status, "paused", True)), speed=_float(_get(status, "playback_speed")),
        status_ts=_int(_get(version, "timestamp_ms")), frame_ts=_int(_get(frame, "timestamp_ms")),
        status_key=status_key,
    )


def full_state_request(device_id: str) -> dict:
    """Первое сообщение после подключения: регистрирует пассивное устройство.

    Версия и время нашего (пустого) состояния — нули: сервер считает его старше любого настоящего и оставляет
    очередь пользователя как есть. is_currently_active=false — мы не становимся играющим устройством.
    """
    version = {"deviceId": device_id, "version": "0", "timestampMs": "0"}
    return {
        "updateFullState": {
            "playerState": {
                "playerQueue": {
                    "currentPlayableIndex": -1, "entityId": "", "entityType": "VARIOUS", "playableList": [],
                    "options": {"repeatMode": "NONE"}, "entityContext": "BASED_ON_ENTITY_BY_DEFAULT",
                    "version": version, "fromOptional": "",
                },
                "status": {"durationMs": "0", "paused": True, "playbackSpeed": 1, "progressMs": "0",
                           "version": version},
            },
            "device": {
                "capabilities": {"canBePlayer": False, "canBeRemoteController": False, "volumeGranularity": 0},
                "info": {"deviceId": device_id, "type": "WEB", "title": "yamusicbot", "appName": "yamusicbot"},
                "volumeInfo": {"volume": 0},
            },
            "isCurrentlyActive": False,
        },
        "rid": str(uuid.uuid4()),
        "playerActionTimestampMs": "0",
        "activityInterceptionType": "DO_NOT_INTERCEPT_BY_DEFAULT",
    }


def _protocols(device_id: str, redirect: dict | None = None) -> tuple[str, ...]:
    info: dict[str, str] = {
        "Ynison-Device-Id": device_id,
        # Да, JSON внутри JSON: иначе сервер не принимает.
        "Ynison-Device-Info": json.dumps({"app_name": "yamusicbot", "type": "1"}),
    }
    if redirect:
        info["Ynison-Redirect-Ticket"] = redirect["ticket"]
        if redirect.get("session_id"):
            info["Ynison-Session-Id"] = str(redirect["session_id"])
    return "Bearer", "v2", urllib.parse.quote(json.dumps(info))


def _headers(token: str) -> dict[str, str]:
    return {"Origin": ORIGIN, "Authorization": f"OAuth {token}"}


def _handshake_error(e: aiohttp.WSServerHandshakeError) -> Exception:
    if e.status in (401, 403):
        return YnisonAuthError(f"HTTP {e.status}")
    if 400 <= e.status < 500:  # адрес или протокол поменялся
        return YnisonProtocolError(f"HTTP {e.status} при подключении: {e.message}")
    return e  # 5xx — временная беда на стороне Яндекса


async def redirect(http: aiohttp.ClientSession, token: str, device_id: str) -> dict:
    """Первый шаг: редиректор выдаёт хост и билет для подключения к состоянию."""
    try:
        async with http.ws_connect(YNISON_URL + REDIRECT_PATH, headers=_headers(token),
                                   protocols=_protocols(device_id), timeout=aiohttp.ClientWSTimeout(ws_close=5),
                                   max_msg_size=1024 * 1024) as ws:
            msg = await ws.receive(timeout=HANDSHAKE_TIMEOUT)
    except aiohttp.WSServerHandshakeError as e:
        raise _handshake_error(e) from e
    if msg.type != aiohttp.WSMsgType.TEXT:
        raise YnisonProtocolError(f"редиректор ответил не текстом: {msg.type.name} {str(msg.data)[:200]}")
    try:
        data = json.loads(msg.data)
    except ValueError as e:
        raise YnisonProtocolError(f"редиректор ответил не JSON: {msg.data[:200]}") from e
    if isinstance(data, dict) and data.get("error"):
        parse_state(data)  # превратит ошибку сервера в YnisonAuthError / YnisonProtocolError
    host, ticket = str(_get(data, "host") or ""), str(_get(data, "redirect_ticket") or "")
    if not host or not ticket:
        raise YnisonProtocolError(f"в ответе редиректора нет host/redirect_ticket: {msg.data[:300]}")
    host = host.removeprefix("wss://").rstrip("/")
    if not _HOST_RE.match(host):
        raise YnisonProtocolError(f"редиректор прислал чужой хост: {host[:100]}")
    keepalive = _int(_get(_get(data, "keep_alive_params"), "keep_alive_time_seconds"), DEFAULT_KEEPALIVE)
    return {"host": host, "ticket": ticket, "session_id": _get(data, "session_id"),
            "keepalive": min(max(keepalive, 5), 120)}


FrameHandler = Callable[[dict, str], Awaitable[None]]


async def listen(http: aiohttp.ClientSession, token: str, device_id: str, on_frame: FrameHandler) -> None:
    """Держит соединение и передаёт каждый кадр в on_frame(разобранный JSON, сырой текст).

    Возвращается, когда сервер закрыл соединение (это нормально: переподключается вызывающий).
    """
    target = await redirect(http, token, device_id)
    try:
        ws = await http.ws_connect(f"{STATE_SCHEME}://{target['host']}{STATE_PATH}", headers=_headers(token),
                                   protocols=_protocols(device_id, target), heartbeat=target["keepalive"],
                                   timeout=aiohttp.ClientWSTimeout(ws_close=5), max_msg_size=MAX_FRAME)
    except aiohttp.WSServerHandshakeError as e:
        raise _handshake_error(e) from e
    async with ws:
        await ws.send_str(json.dumps(full_state_request(device_id)))
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                try:
                    frame = json.loads(msg.data)
                except ValueError as e:
                    raise YnisonProtocolError(f"кадр не JSON: {msg.data[:200]}") from e
                await on_frame(frame, msg.data)
            elif msg.type == aiohttp.WSMsgType.ERROR:
                raise ws.exception() or ConnectionError("ошибка websocket")
        if ws.close_code and ws.close_code not in (1000, 1001, 1006):
            reason = f"сервер закрыл соединение: код {ws.close_code}"
            if ws.close_code in (4001, 4003, 4401, 4403):
                raise YnisonAuthError(reason)
