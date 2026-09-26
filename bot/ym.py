"""Обёртка над Яндекс Музыкой: библиотека yandex-music + загрузка своих треков (UGC)."""

from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import aiohttp
from yandex_music import Album, ClientAsync, DownloadInfo, Playlist, Search, Track
from yandex_music.exceptions import NetworkError, UnauthorizedError, YandexMusicError

from bot.audio import ConversionError, ffmpeg_available, reencode_mp3, safe_filename, tag_mp3
from bot.errors import log_failure

log = logging.getLogger(__name__)

START_RETRY_INTERVAL = 20  # сек: не долбить Яндекс повторными попытками на каждое сообщение
API_BASE_URLS = ("https://api.music.yandex.ru", "https://api.music.yandex.net")
# Заголовки, с которыми к этим хостам ходят приложение (.net) и новый сайт (.ru).
API_HEADERS = {
    "api.music.yandex.net": {"X-Yandex-Music-Client": "YandexMusicAndroid/24023621"},
    "api.music.yandex.ru": {
        "X-Yandex-Music-Client": "YandexMusicWebNext/1.0.0",
        "X-Requested-With": "XMLHttpRequest",
        "X-Yandex-Music-Without-Invocation-Info": "1",
        "Origin": "https://music.yandex.ru",
        "Referer": "https://music.yandex.ru/",
        "Accept": "application/json",
    },
}
TOO_MANY_FILES = "TOO_MANY_FILES"
# Токен уходит только на эти домены (и на адреса API из настроек): post-target приходит в ответе сервера,
# и чужой адрес в нём не должен получить вход в аккаунт.
YANDEX_DOMAINS = ("yandex.ru", "yandex.net", "yandex.com")


def _host(url: str) -> str:
    return urlsplit(url).netloc


def _is_yandex(url: str) -> bool:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and any(host == d or host.endswith("." + d) for d in YANDEX_DOMAINS)


def _parse_upload_target(body: str) -> dict | str | None:
    """Ответ на запрос адреса загрузки: dict с post-target, TOO_MANY_FILES или None (не то)."""
    try:
        payload = json.loads(body)
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    result = payload.get("result")
    if isinstance(result, dict):  # ответы API обёрнуты в {"invocationInfo": …, "result": …}
        payload, result = result, result.get("result")
    if isinstance(result, str) and result.upper().replace("-", "_") == TOO_MANY_FILES:
        return TOO_MANY_FILES
    return payload if payload.get("post-target") else None


def _describe_body(body: str) -> str:
    body = " ".join(body.split())
    if body.startswith("<"):
        return "(HTML-страница вместо ответа API)"
    return body[:200]


class UploadError(RuntimeError):
    pass


class UnsupportedMediaError(UploadError):
    """Яндекс не принял формат файла (HTTP 415 UNSUPPORTED_MEDIA_TYPE)."""


class UploadEndpointError(UploadError):
    """Адрес загрузки не отвечает как раньше — скорее всего, Яндекс изменил свой сайт (API неофициальный).

    str() — понятное объяснение для пользователя, details — ответы сервера для владельца бота.
    """

    def __init__(self, details: str) -> None:
        super().__init__(
            "Загрузка в Яндекс Музыку сейчас не работает — похоже, Яндекс изменил свой сайт. "
            "Владелец бота получит уведомление; попробуйте позже."
        )
        self.details = details


UNSUPPORTED_MEDIA = (
    "Яндекс не принял формат файла — ему нужен обычный MP3. Похоже, внутри файла другой формат "
    "(так бывает у скачанного с YouTube)"
)


YANDEX_UNREACHABLE = ("Сервер бота сейчас не может связаться с Яндекс Музыкой — она не отвечает. Обычно это временно. "
                      "Входить заново не нужно: как только связь вернётся, всё заработает само.")


def is_network_error(e: BaseException) -> bool:
    """Яндекс не ответил или до него нет связи (в отличие от ошибок входа и ответа Яндекса)."""
    return isinstance(e, NetworkError | aiohttp.ClientConnectionError | TimeoutError | ConnectionError)


class YandexNotReady(RuntimeError):
    """Подключиться к Яндекс Музыке не удалось; текст — объяснение для человека.

    network — дело в связи с Яндексом, а не во входе: подключаться заново бесполезно, надо подождать.
    """

    def __init__(self, text: str, network: bool = False) -> None:
        super().__init__(text)
        self.network = network


def explain_start_error(e: Exception) -> str:
    if isinstance(e, UnauthorizedError):
        # Библиотека выдаёт это и на 401, и на 403: второй бывает из-за блокировки по IP, а не входа.
        return (
            "Яндекс Музыка не приняла вход: он устарел или был отозван в настройках Яндекс ID "
            "(реже — Яндекс не пускает запросы с сервера бота). Подключите аккаунт заново."
        )
    if is_network_error(e):
        return YANDEX_UNREACHABLE
    return f"Не удалось подключиться к Яндекс Музыке: {type(e).__name__}: {e}"


class TrackUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True)
class UploadResult:
    ugc_track_id: str | None
    server_reply: str
    note: str | None = None  # что пришлось сделать с файлом, чтобы Яндекс его принял


def track_artists(track: Track) -> str:
    names = [a.name for a in (track.artists or []) if a.name]
    return ", ".join(names) or "Неизвестный исполнитель"


def track_title(track: Track) -> str:
    title = track.title or "Без названия"
    return f"{title} ({track.version})" if track.version else title


def track_album(track: Track) -> Album | None:
    return track.albums[0] if track.albums else None


def cover_url(uri: str | None, size: str = "200x200") -> str | None:
    """Адрес картинки Яндекса по шаблону вида avatars.yandex.net/.../%%."""
    if not uri:
        return None
    return "https://" + uri.replace("%%", size).removeprefix("https://").removeprefix("//")


def tagged_filename(track: Track) -> str:
    return safe_filename(f"{track_artists(track)} - {track_title(track)}") + ".mp3"


class YandexMusic:
    def __init__(
        self,
        token: str,
        *,
        max_bitrate: int = 320,
        api_base_urls: Sequence[str] = API_BASE_URLS,
    ) -> None:
        self._token = token
        self._max_bitrate = max_bitrate
        self._api_base_urls = [u.rstrip("/") for u in api_base_urls]
        self._client: ClientAsync | None = None
        self._session: aiohttp.ClientSession | None = None
        self.uid: int | None = None
        self.login: str | None = None
        self.has_plus = False
        self.start_error: str | None = None
        self.start_network = False  # прошлая неудача — из-за связи, а не входа
        self._start_lock = asyncio.Lock()
        self._last_attempt = 0.0

    @property
    def client(self) -> ClientAsync:
        if self._client is None:
            raise RuntimeError("YandexMusic.start() ещё не вызывался")
        return self._client

    @property
    def ready(self) -> bool:
        return self._client is not None

    async def start(self) -> None:
        client = await ClientAsync(self._token).init()
        me = client.me
        if me is None or me.account is None or me.account.uid is None:
            raise YandexMusicError("Яндекс не вернул данные аккаунта")
        self.uid = me.account.uid
        self.login = me.account.login
        self.has_plus = bool(me.plus and me.plus.has_plus)
        self._client = client

    async def ensure_started(self) -> None:
        """Подключается к Яндексу, если ещё не подключены. Неудача — YandexNotReady с объяснением.

        Бот при этом продолжает работать: так он может сказать в чате, что не так, вместо того чтобы молчать.
        Повторная попытка — не чаще раза в START_RETRY_INTERVAL секунд.
        """
        if self.ready:
            return
        async with self._start_lock:
            if self.ready:
                return
            if self.start_error and time.monotonic() - self._last_attempt < START_RETRY_INTERVAL:
                raise YandexNotReady(self.start_error, self.start_network)
            self._last_attempt = time.monotonic()
            try:
                await self.start()
            except Exception as e:
                self.start_error, self.start_network = explain_start_error(e), is_network_error(e)
                log_failure(log, "%s", self.start_error, exc=e)
                raise YandexNotReady(self.start_error, self.start_network) from e
            self.start_error, self.start_network = None, False
            log.info("Яндекс Музыка подключена: uid %s, Плюс: %s", self.uid, self.has_plus)

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    def _http(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15 * 60), trust_env=True)
        return self._session

    def _auth(self, url: str) -> dict[str, str]:
        """Заголовок с токеном — только для Яндекса по HTTPS и для адресов API из настроек."""
        origin = urlsplit(url)[:2]
        trusted = _is_yandex(url) or any(origin == urlsplit(base)[:2] for base in self._api_base_urls)
        if not trusted:
            log.warning("Не отправляю токен на %s: это не адрес Яндекса", _host(url))
            return {}
        return {"Authorization": f"OAuth {self._token}"}

    # ---------- каталог ----------

    async def search(self, query: str, type_: str = "all", page: int = 0) -> Search | None:
        return await self.client.search(query, type_=type_, page=page)

    async def get_tracks(self, track_ids: Sequence[str]) -> list[Track]:
        result: list[Track] = []
        for i in range(0, len(track_ids), 100):
            result.extend(await self.client.tracks(list(track_ids[i:i + 100])))
        return result

    async def get_track(self, track_id: str) -> Track | None:
        tracks = await self.client.tracks([track_id])
        return tracks[0] if tracks else None

    async def get_album(self, album_id: str) -> Album | None:
        return await self.client.albums_with_tracks(album_id)

    async def get_artist_tracks(self, artist_id: str, limit: int = 50) -> list[Track]:
        page = await self.client.artists_tracks(artist_id, page_size=limit)
        return list(page.tracks) if page and page.tracks else []

    # ---------- плейлисты и лайки ----------

    async def get_my_playlists(self) -> list[Playlist]:
        return await self.client.users_playlists_list()

    async def music_history(self):
        """История прослушивания (раздел «История» в приложении): дни → откуда играло → треки."""
        return await self.client.music_history()

    async def music_history_raw(self) -> Any:
        """Та же история как есть, без разбора — чтобы владелец мог сверить формат ответа Яндекса."""
        return await self.client._request.get(f"{self.client.base_url}/music-history", {"fullModelsCount": 0})

    async def get_playlist(self, kind: int | str, owner: int | str | None = None) -> Playlist | None:
        playlist = await self.client.users_playlists(kind, owner if owner is not None else self.uid)
        return playlist[0] if isinstance(playlist, list) else playlist

    async def get_playlist_by_uuid(self, uuid: str) -> Playlist | None:
        return await self.client.playlist(uuid)

    async def create_playlist(self, title: str) -> Playlist:
        playlist = await self.client.users_playlists_create(title, visibility="private")
        if playlist is None:
            raise YandexMusicError("Яндекс не вернул созданный плейлист")
        return playlist

    async def delete_playlist(self, kind: int) -> bool:
        return await self.client.users_playlists_delete(kind)

    async def rename_playlist(self, kind: int, title: str) -> Playlist | None:
        return await self.client.users_playlists_name(kind, title)

    async def remove_from_playlist(self, kind: int, index: int, track_id: str) -> Playlist | None:
        """Удаляет трек по позиции; позицию сверяем с id, чтобы не удалить не тот трек, если плейлист изменился."""
        playlist = await self.get_playlist(kind)
        if playlist is None:
            raise YandexMusicError("Плейлист не найден")
        ids = [str(s.id) for s in playlist.tracks or []]
        if not (0 <= index < len(ids) and ids[index] == track_id):
            if track_id not in ids:
                raise YandexMusicError("Трека уже нет в плейлисте")
            index = ids.index(track_id)
        return await self.client.users_playlists_delete_track(
            kind, index, index + 1, revision=playlist.revision or 1,
        )

    async def add_to_playlist(self, kind: int, track: Track) -> Playlist | None:
        playlist = await self.get_playlist(kind)
        if playlist is None:
            raise YandexMusicError("Плейлист не найден")
        album = track_album(track)
        if album is None or album.id is None:
            raise YandexMusicError("У трека нет альбома — Яндекс не даёт добавить такой трек в плейлист")
        return await self.client.users_playlists_insert_track(
            kind, track.id, album.id, at=playlist.track_count or 0, revision=playlist.revision or 1,
        )

    async def playlist_track_ids(self, kind: int) -> set[str]:
        playlist = await self.get_playlist(kind)
        return {str(s.id) for s in playlist.tracks or []} if playlist is not None else set()

    async def move_to_top(self, kind: int, track_ids: Sequence[str], playlist: Playlist | None = None) -> bool:
        """Ставит треки своего плейлиста в самое начало, в заданном порядке. False — таких треков в нём нет.

        Одним запросом: сначала вставляем треки наверх, потом удаляем их прежние позиции. Если Яндекс вдруг
        применит только часть операций, трек в худшем случае окажется в плейлисте дважды, но не пропадёт.
        """
        playlist = playlist or await self.get_playlist(kind)
        if playlist is None:
            raise YandexMusicError("Плейлист не найден")
        shorts = list(playlist.tracks or [])
        positions: dict[str, int] = {}
        for i, short in enumerate(shorts):
            positions.setdefault(str(short.id), i)
        wanted = list(dict.fromkeys(t for t in track_ids if t in positions))
        if not wanted:
            return False
        if [str(s.id) for s in shorts[:len(wanted)]] == wanted:
            return True  # уже наверху

        items = []
        for track_id in wanted:
            short = shorts[positions[track_id]]
            item: dict[str, object] = {"id": short.id}
            if short.album_id:  # у загруженных треков альбома нет
                item["albumId"] = short.album_id
            items.append(item)
        ops: list[dict[str, object]] = [{"op": "insert", "at": 0, "tracks": items}]
        for pos in sorted((positions[t] for t in wanted), reverse=True):
            ops.append({"op": "delete", "from": pos + len(items), "to": pos + len(items) + 1})
        await self.client.users_playlists_change(kind, json.dumps(ops), revision=playlist.revision or 1)
        return True

    async def get_liked_track_ids(self) -> list[str]:
        likes = await self.client.users_likes_tracks()
        return [s.track_id for s in likes.tracks] if likes and likes.tracks else []

    async def like(self, track_id: str) -> bool:
        return await self.client.users_likes_tracks_add(track_id)

    async def unlike(self, track_id: str) -> bool:
        return await self.client.users_likes_tracks_remove(track_id)

    # ---------- скачивание ----------

    async def _best_download_info(self, track: Track, max_bitrate: int | None = None) -> DownloadInfo:
        """Лучший MP3 не выше max_bitrate (по умолчанию MAX_BITRATE) с уже полученной прямой ссылкой."""
        limit = min(max_bitrate or self._max_bitrate, self._max_bitrate)
        if track.available is False:
            raise TrackUnavailableError("Трек недоступен для прослушивания")
        infos = await track.get_download_info_async()
        mp3 = [i for i in infos if i.codec == "mp3" and not i.preview] or [i for i in infos if i.codec == "mp3"]
        if not mp3:
            raise TrackUnavailableError("Для трека нет MP3 для скачивания")
        allowed = [i for i in mp3 if i.bitrate_in_kbps <= limit]
        best = max(allowed, key=lambda i: i.bitrate_in_kbps) if allowed else min(mp3, key=lambda i: i.bitrate_in_kbps)
        # Прямую ссылку берём только для выбранного варианта, а не для всех сразу.
        await best.get_direct_link_async()
        return best

    async def direct_link(self, track: Track, max_bitrate: int | None = None) -> str:
        """Временная прямая ссылка на MP3 (для плеера в мини-приложении)."""
        return (await self._best_download_info(track, max_bitrate)).direct_link

    async def download(self, track: Track, max_bitrate: int | None = None) -> tuple[bytes, int]:
        """Скачивает MP3 в лучшем доступном качестве, но не выше max_bitrate."""
        best = await self._best_download_info(track, max_bitrate)
        return await best.download_bytes_async(), best.bitrate_in_kbps

    async def download_tagged(self, track: Track, max_bitrate: int | None = None) -> tuple[bytes, int]:
        """MP3 с тегами (исполнитель, название, альбом, год) и обложкой."""
        (data, bitrate), cover = await asyncio.gather(
            self.download(track, max_bitrate), self.download_cover(track, "600x600"),
        )
        album = track_album(track)
        data = tag_mp3(
            data,
            title=track_title(track),
            artist=track_artists(track),
            album=album.title if album else None,
            year=album.year if album else None,
            cover=cover,
        )
        return data, bitrate

    async def download_cover(self, track: Track, size: str = "400x400") -> bytes | None:
        if not track.cover_uri:
            return None
        try:
            return await track.download_cover_bytes_async(size)
        except YandexMusicError:
            log.warning("Не удалось скачать обложку трека %s", track.id, exc_info=True)
            return None

    # ---------- загрузка своих треков ----------

    async def _get_upload_target(self, playlist_kind: int, filename: str) -> dict:
        """Одноразовый адрес для загрузки файла: {"post-target": …, "ugc-track-id": …}.

        Новый сайт (music.yandex.ru v4) делает это так:
            loaderResource.getUploadUrl({playlistId: `${uid}:${kind}`, uid, path: fileName})
        — это POST на loader/upload-url (на GET сервер отвечает 405 «method GET is not supported»).
        Параметры кладём и в адрес, и в тело формы: Java-сервлет загрузчика читает их откуда угодно.
        Старый адрес сайта handlers/ugc-upload.jsx Яндекс убрал в 2026 году.
        """
        if self.uid is None:
            raise UploadError("Яндекс Музыка ещё не подключена")
        playlist_id = f"{self.uid}:{playlist_kind}"
        params = {
            "uid": str(self.uid),
            "playlist-id": playlist_id,
            "playlistId": playlist_id,  # имя параметра в коде сайта; сервер лишний проигнорирует
            "path": filename,
        }

        attempts: list[str] = []
        statuses: list[int] = []
        for base in self._api_base_urls:
            url = f"{base}/loader/upload-url"
            headers = {**API_HEADERS.get(_host(base), {}), **self._auth(url)}
            try:
                async with self._http().post(url, params=params, data=params, headers=headers) as resp:
                    body = await resp.text()
                    status = resp.status
            except aiohttp.ClientError as e:
                attempts.append(f"{_host(url)}: сетевая ошибка {e}")
                continue
            payload = _parse_upload_target(body)
            if payload == TOO_MANY_FILES:
                raise UploadError("Яндекс не принимает больше файлов: достигнут лимит загруженных треков в аккаунте")
            if payload is not None:
                log.info("Адрес загрузки получен через %s", url)
                return payload
            statuses.append(status)
            attempts.append(f"{_host(url)}{urlsplit(url).path}: HTTP {status} {_describe_body(body)}")
        details = "Яндекс не выдал адрес для загрузки. Ответы: " + "; ".join(attempts)
        if not statuses:  # ни одного ответа — это связь сервера с Яндексом, а не изменения у Яндекса
            raise UploadError(details)
        if all(s in (401, 403) for s in statuses):  # не принят вход именно этого пользователя
            raise UploadError("Яндекс не принял ваш вход при загрузке — подключите аккаунт заново: /login")
        raise UploadEndpointError(details)


    async def upload_track(self, playlist_kind: int, filename: str, data: bytes) -> UploadResult:
        """Загружает свой аудиофайл в плейлист (как кнопка «Загрузить трек» на music.yandex.ru).

        1. Яндекс выдаёт одноразовый адрес для загрузки (post-target) — см. _get_upload_target;
        2. POST файла на этот адрес в multipart-поле «file».
        После этого Яндекс ещё пару минут обрабатывает файл, и трек появляется в плейлисте.

        Если Яндекс не принял файл как MP3 (HTTP 415), перекодируем его в обычный MP3 и пробуем ещё раз.
        """
        try:
            return await self._upload_once(playlist_kind, filename, data)
        except UnsupportedMediaError as e:
            if not ffmpeg_available():
                raise UploadError(f"{UNSUPPORTED_MEDIA}. Установите ffmpeg — бот сам переведёт файл в MP3.") from e
            log.warning("Яндекс не принял %s как MP3 — перекодирую и пробую ещё раз", filename)
            try:
                fixed = await reencode_mp3(data)
            except ConversionError as conv:
                raise UploadError(f"{UNSUPPORTED_MEDIA}, а перекодировать его не вышло: {conv}") from conv
        try:
            result = await self._upload_once(playlist_kind, filename, fixed)
        except UnsupportedMediaError as e:
            raise UploadError(f"{UNSUPPORTED_MEDIA}; не помогла и перекодировка в MP3.") from e
        return UploadResult(result.ugc_track_id, result.server_reply,
                            note="Яндекс не принял файл как есть — перекодирован в MP3 320 kbps")

    async def _upload_once(self, playlist_kind: int, filename: str, data: bytes) -> UploadResult:
        http = self._http()
        payload = await self._get_upload_target(playlist_kind, filename)
        target = payload["post-target"]

        # quote_fields=False: имя файла уходит как в браузере (UTF-8), а не %D0%9A...
        form = aiohttp.FormData(quote_fields=False)
        form.add_field(
            "file", data, filename=filename,
            content_type=mimetypes.guess_type(filename)[0] or "application/octet-stream",
        )
        try:
            async with http.post(target, data=form, headers=self._auth(target)) as resp:
                reply = (await resp.text()).strip()
                status = resp.status
        except aiohttp.ClientError as e:
            raise UploadError(f"Сетевая ошибка при отправке файла: {e}") from e
        if status == 415 or "UNSUPPORTED_MEDIA_TYPE" in reply:
            raise UnsupportedMediaError(reply[:300])
        if status in (404, 405, 410, 501):  # адреса для файла больше нет — изменился сам способ загрузки
            raise UploadEndpointError(f"Адрес для файла ответил HTTP {status}: {reply[:300]}")
        if status >= 300:
            raise UploadError(f"Яндекс отклонил файл (HTTP {status}): {reply[:300]}")
        log.info("Загружен %s в плейлист %s: %s", filename, playlist_kind, reply[:100])
        return UploadResult(ugc_track_id=payload.get("ugc-track-id"), server_reply=reply)
