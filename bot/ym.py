"""Обёртка над Яндекс Музыкой: библиотека yandex-music + загрузка своих треков (UGC)."""

from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import random
import time
from collections.abc import Sequence
from dataclasses import dataclass

import aiohttp
from yandex_music import Album, ClientAsync, DownloadInfo, Playlist, Search, Track
from yandex_music.exceptions import NetworkError, UnauthorizedError, YandexMusicError

from bot.audio import safe_filename, tag_mp3

log = logging.getLogger(__name__)

WEB_BASE_URL = "https://music.yandex.ru"


START_RETRY_INTERVAL = 20  # сек: не долбить Яндекс повторными попытками на каждое сообщение


class UploadError(RuntimeError):
    pass


class YandexNotReady(RuntimeError):
    """Бот работает, но подключиться к Яндекс Музыке не удалось; текст — объяснение для человека."""


def explain_start_error(e: Exception) -> str:
    if isinstance(e, UnauthorizedError):
        # Библиотека выдаёт это и на 401, и на 403: второй бывает из-за блокировки по IP, а не токена.
        return (
            "Яндекс Музыка отклонила запрос: токен YANDEX_MUSIC_TOKEN неверный или устарел, "
            "либо Яндекс не пускает запросы с IP этого сервера."
        )
    if isinstance(e, NetworkError):
        return f"Сервер не может связаться с Яндекс Музыкой (api.music.yandex.net): {e}"
    return f"Не удалось подключиться к Яндекс Музыке: {type(e).__name__}: {e}"


class TrackUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True)
class UploadResult:
    ugc_track_id: str | None
    server_reply: str


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
    def __init__(self, token: str, *, max_bitrate: int = 320, web_base_url: str = WEB_BASE_URL) -> None:
        self._token = token
        self._max_bitrate = max_bitrate
        self._web_base_url = web_base_url.rstrip("/")
        self._client: ClientAsync | None = None
        self._session: aiohttp.ClientSession | None = None
        self.uid: int | None = None
        self.login: str | None = None
        self.has_plus = False
        self.start_error: str | None = None
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
            raise YandexMusicError("Не удалось получить аккаунт: проверьте YANDEX_MUSIC_TOKEN")
        self.uid = me.account.uid
        self.login = me.account.login
        self.has_plus = bool(me.plus and me.plus.has_plus)
        self._client = client

    async def ensure_started(self) -> None:
        """Подключается к Яндексу, если ещё не подключены. Неудача — YandexNotReady с объяснением.

        Бот при этом продолжает работать: так он может сказать в чате, что не так, вместо того чтобы молчать.
        """
        if self.ready:
            return
        async with self._start_lock:
            if self.ready:
                return
            if self.start_error and time.monotonic() - self._last_attempt < START_RETRY_INTERVAL:
                raise YandexNotReady(self.start_error)
            self._last_attempt = time.monotonic()
            try:
                await self.start()
            except Exception as e:
                self.start_error = explain_start_error(e)
                log.error("%s (%r)", self.start_error, e)
                raise YandexNotReady(self.start_error) from e
            self.start_error = None
            log.info("Яндекс Музыка подключена: %s (uid %s), Плюс: %s", self.login, self.uid, self.has_plus)

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    def _http(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15 * 60),
                headers={"Authorization": f"OAuth {self._token}"},
                trust_env=True,
            )
        return self._session

    # ---------- каталог ----------

    async def search(self, query: str, type_: str = "all") -> Search | None:
        return await self.client.search(query, type_=type_)

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

    async def get_liked_track_ids(self) -> list[str]:
        likes = await self.client.users_likes_tracks()
        return [s.track_id for s in likes.tracks] if likes and likes.tracks else []

    async def like(self, track_id: str) -> bool:
        return await self.client.users_likes_tracks_add(track_id)

    async def unlike(self, track_id: str) -> bool:
        return await self.client.users_likes_tracks_remove(track_id)

    # ---------- скачивание ----------

    async def _best_download_info(self, track: Track) -> DownloadInfo:
        """Лучший MP3 (но не выше MAX_BITRATE) с уже полученной прямой ссылкой."""
        if track.available is False:
            raise TrackUnavailableError("Трек недоступен для прослушивания")
        infos = await track.get_download_info_async()
        mp3 = [i for i in infos if i.codec == "mp3" and not i.preview] or [i for i in infos if i.codec == "mp3"]
        if not mp3:
            raise TrackUnavailableError("Для трека нет MP3 для скачивания")
        allowed = [i for i in mp3 if i.bitrate_in_kbps <= self._max_bitrate]
        best = max(allowed, key=lambda i: i.bitrate_in_kbps) if allowed else min(mp3, key=lambda i: i.bitrate_in_kbps)
        # Прямую ссылку берём только для выбранного варианта, а не для всех сразу.
        await best.get_direct_link_async()
        return best

    async def direct_link(self, track: Track) -> str:
        """Временная прямая ссылка на MP3 (для плеера в мини-приложении)."""
        return (await self._best_download_info(track)).direct_link

    async def download(self, track: Track) -> tuple[bytes, int]:
        """Скачивает MP3 в лучшем доступном качестве (но не выше MAX_BITRATE)."""
        best = await self._best_download_info(track)
        return await best.download_bytes_async(), best.bitrate_in_kbps

    async def download_tagged(self, track: Track) -> tuple[bytes, int]:
        """MP3 с тегами (исполнитель, название, альбом, год) и обложкой."""
        data, bitrate = await self.download(track)
        album = track_album(track)
        data = tag_mp3(
            data,
            title=track_title(track),
            artist=track_artists(track),
            album=album.title if album else None,
            year=album.year if album else None,
            cover=await self.download_cover(track, "600x600"),
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

    async def upload_track(self, playlist_kind: int, filename: str, data: bytes) -> UploadResult:
        """Загружает свой аудиофайл в плейлист (как кнопка «Загрузить трек» на music.yandex.ru).

        1. GET /handlers/ugc-upload.jsx — Яндекс выдаёт одноразовый адрес для загрузки (post-target);
        2. POST файла на этот адрес в multipart-поле «file».
        После этого Яндекс ещё пару минут обрабатывает файл, и трек появляется в плейлисте.
        """
        http = self._http()
        params = {
            "filename": filename,
            "kind": str(playlist_kind),
            "visibility": "private",
            "external-domain": "music.yandex.ru",
            "overembed": "false",
            "ncrnd": repr(random.random()),
        }
        try:
            async with http.get(f"{self._web_base_url}/handlers/ugc-upload.jsx", params=params) as resp:
                body = await resp.text()
                status = resp.status
        except aiohttp.ClientError as e:
            raise UploadError(f"Сетевая ошибка при запросе адреса загрузки: {e}") from e
        if body.lstrip().startswith("<"):
            # Вместо JSON пришла страница сайта: старый адрес загрузки Яндекс убрал вместе со старым сайтом.
            raise UploadError(
                f"Яндекс изменил способ загрузки треков: старый адрес больше не работает (HTTP {status}). "
                "Нужна новая версия бота — перешлите это сообщение разработчику."
            )
        if status != 200:
            raise UploadError(f"Яндекс не выдал адрес для загрузки (HTTP {status}): {body[:300]}")
        try:
            payload = json.loads(body)
        except ValueError as e:
            raise UploadError(f"Неожиданный ответ Яндекса вместо адреса загрузки: {body[:300]}") from e
        target = payload.get("post-target") if isinstance(payload, dict) else None
        if not target:
            raise UploadError(f"Яндекс не выдал адрес для загрузки: {body[:300]}")

        # quote_fields=False: имя файла уходит как в браузере (UTF-8), а не %D0%9A...
        form = aiohttp.FormData(quote_fields=False)
        form.add_field(
            "file", data, filename=filename,
            content_type=mimetypes.guess_type(filename)[0] or "application/octet-stream",
        )
        try:
            async with http.post(target, data=form) as resp:
                reply = (await resp.text()).strip()
                status = resp.status
        except aiohttp.ClientError as e:
            raise UploadError(f"Сетевая ошибка при отправке файла: {e}") from e
        if status >= 300:
            raise UploadError(f"Яндекс отклонил файл (HTTP {status}): {reply[:300]}")
        log.info("Загружен %s в плейлист %s: %s", filename, playlist_kind, reply[:100])
        return UploadResult(ugc_track_id=payload.get("ugc-track-id"), server_reply=reply)
