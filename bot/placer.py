"""Свежие загрузки — в начало плейлиста.

Яндекс кладёт загруженный файл в конец плейлиста, да и то через минуту-другую, когда обработает его.
Поэтому после загрузки следим за плейлистом и, как только трек появился, переставляем его наверх:
так его не надо искать, пролистывая весь плейлист.

Загрузки, сделанные подряд (одна «пачка»), встают наверх в том порядке, в каком их загружали,
а более поздняя пачка — над более ранней.

Бывает, что Яндекс принимает файл, но в плейлисте трек так и не появляется (так было с первой загрузкой после
перезапуска бота), а повторная загрузка того же файла проходит. Поэтому файл какое-то время хранится в памяти,
и если трек не появился за RESEND_AFTER, бот один раз отправляет его ещё раз сам.

Итог каждой загрузки (future из after_upload): True — трек наверху; False — трек в плейлисте, но переставить
не вышло; None — Яндекс так и не добавил его в плейлист.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import time
from dataclasses import dataclass, field

from yandex_music import TrackShort

from bot import net
from bot.errors import log_failure, spawn
from bot.ym import YandexMusic

log = logging.getLogger(__name__)

FIRST_CHECK = 5  # сек после загрузки до первой проверки плейлиста
POLL_INTERVAL = 10
SLOW_POLL_AFTER, SLOW_POLL_INTERVAL = 120, 30  # сек: не появился за 2 минуты — проверяем реже
GIVE_UP_AFTER = 15 * 60  # сек после последней загрузки: дольше Яндекс не обрабатывает
GROUP_GAP = 120  # загрузки чаще, чем раз в две минуты, считаем одной пачкой
MAX_FAILURES = 5
OWN_SOURCES = {"OWN", "OWN_REPLACED_TO_UGC"}
# Трек так и не появился в плейлисте — через столько секунд отправить файл ещё раз (большим — позже, до 10 минут).
RESEND_AFTER = 4 * 60
RESEND_MAX_WAIT = 10 * 60
MAX_HELD_BYTES = 200 * 1024 * 1024  # сколько байт файлов держим в памяти ради повторной отправки (на всех)


def _resend_after(size: int) -> float:
    """Большие файлы Яндекс обрабатывает дольше: +1 минута на каждые 10 МБ."""
    return min(RESEND_AFTER + size / (10 * 1024 * 1024) * 60, RESEND_MAX_WAIT)


@dataclass
class _Upload:
    seq: int
    group: int
    ugc_ids: list[str]  # id из ответа загрузки (и повторной, если была)
    done: asyncio.Future
    started: float = 0.0
    resend: tuple[str, bytes] | None = None  # (имя, файл) — отправить ещё раз, если трек не появится
    resent: bool = False
    track: str | None = None  # id трека в плейлисте, когда он там появился
    placed: bool = False


@dataclass
class _Watch:
    ym: YandexMusic
    kind: int
    known: set[str] | None  # что было в плейлисте до пачки; None — не удалось узнать
    uploads: list[_Upload] = field(default_factory=list)
    group: int = 0
    last_added: float = 0.0
    failures: int = 0
    task: asyncio.Task | None = None


def _is_uploaded(short: TrackShort) -> bool:
    """Похоже на загруженный пользователем трек: у таких нет альбома (и источник OWN, если он известен)."""
    if short.album_id:
        return False
    source = getattr(short.track, "track_source", None) if short.track is not None else None
    return source is None or source in OWN_SOURCES


def _matches(short: TrackShort, ugc_id: str) -> bool:
    return ugc_id in (str(short.id), short.track_id, str(getattr(short.track, "id", "")))


class TopPlacer:
    def __init__(self) -> None:
        self._watches: dict[tuple[int, int], _Watch] = {}
        self._seq = itertools.count(1)
        self._held = 0  # байт файлов в памяти ради повторной отправки

    @staticmethod
    def _key(ym: YandexMusic, kind: int) -> tuple[int, int]:
        return ym.uid or 0, int(kind)

    async def before_upload(self, ym: YandexMusic, kind: int) -> set[str] | None:
        """Какие треки уже есть в плейлисте: вызывать перед загрузкой (для пачки снимок берётся один раз)."""
        watch = self._watches.get(self._key(ym, kind))
        if watch is not None:
            return watch.known
        try:
            return await ym.playlist_track_ids(kind)
        except Exception as e:
            log_failure(log, "Не удалось получить плейлист %s перед загрузкой", kind, exc=e)
            return None  # тогда узнаём трек только по его id из ответа загрузки

    def after_upload(self, ym: YandexMusic, kind: int, known: set[str] | None, ugc_id: str | None,
                     resend: tuple[str, bytes] | None = None) -> asyncio.Future:
        """Трек загружен — поставить его наверх, когда появится (итог — см. описание модуля).

        resend — (имя, файл): если трек так и не появится в плейлисте, бот отправит его ещё раз сам."""
        if resend is not None and self._held + len(resend[1]) > MAX_HELD_BYTES:
            resend = None  # памяти жалко: такие файлы при сбое человек пришлёт ещё раз сам
        if resend is not None:
            self._held += len(resend[1])
        key = self._key(ym, kind)
        now = time.monotonic()
        seq = next(self._seq)
        watch = self._watches.get(key)
        if watch is None:
            watch = self._watches[key] = _Watch(ym, int(kind), known, group=seq)
        elif now - watch.last_added > GROUP_GAP:
            watch.group = seq
        watch.ym = ym
        watch.last_added = now
        upload = _Upload(seq, watch.group, [str(ugc_id)] if ugc_id else [], asyncio.get_running_loop().create_future(),
                         started=now, resend=resend)
        watch.uploads.append(upload)
        if watch.task is None or watch.task.done():
            watch.task = spawn(self._run(key, watch), f"наверх плейлиста {watch.kind}")
        return upload.done

    async def _run(self, key: tuple[int, int], watch: _Watch) -> None:
        with net.background():  # опрос плейлиста — фон: уступает запросам людей
            await self._watch(key, watch)

    async def _watch(self, key: tuple[int, int], watch: _Watch) -> None:
        try:
            await asyncio.sleep(FIRST_CHECK)
            while any(not u.done.done() for u in watch.uploads):
                if time.monotonic() - watch.last_added > GIVE_UP_AFTER:
                    break  # что не появилось — в лог ниже
                try:
                    await self._step(watch)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    watch.failures += 1
                    log_failure(log, "Не удалось поднять загрузки наверх плейлиста %s (попытка %s)",
                                watch.kind, watch.failures, exc=e)
                    if watch.failures >= MAX_FAILURES:
                        break
                if all(u.done.done() for u in watch.uploads):
                    break
                slow = time.monotonic() - watch.last_added > SLOW_POLL_AFTER
                await asyncio.sleep(SLOW_POLL_INTERVAL if slow else POLL_INTERVAL)
        finally:
            for upload in watch.uploads:
                self._release(upload)
                if not upload.done.done():
                    upload.done.set_result(None if upload.track is None else False)
                if upload.track is None:
                    log.warning("Яндекс так и не добавил загруженный трек в плейлист %s (ugc %s%s)", watch.kind,
                                ", ".join(upload.ugc_ids) or "?", ", файл отправлялся дважды" if upload.resent else "")
            if self._watches.get(key) is watch:
                del self._watches[key]

    def _release(self, upload: _Upload) -> None:
        if upload.resend is not None:
            self._held -= len(upload.resend[1])
            upload.resend = None

    async def _resend_missing(self, watch: _Watch) -> None:
        """Трек так и не появился в плейлисте — отправить файл ещё раз (один раз на загрузку)."""
        now = time.monotonic()
        for upload in watch.uploads:
            if upload.track is not None or upload.resend is None:
                continue
            name, data = upload.resend
            if now - upload.started < _resend_after(len(data)):
                continue
            self._release(upload)
            upload.resent = True
            log.warning("Яндекс за %s мин так и не показал в плейлисте %s загруженный трек «%s» (ugc %s) — "
                        "отправляю файл ещё раз", int((now - upload.started) // 60), watch.kind, name,
                        ", ".join(upload.ugc_ids) or "?")
            result = await watch.ym.upload_track(watch.kind, name, data)
            if result.ugc_track_id:
                upload.ugc_ids.append(str(result.ugc_track_id))
            upload.started = watch.last_added = time.monotonic()  # новое окно ожидания

    async def _step(self, watch: _Watch) -> None:
        playlist = await watch.ym.get_playlist(watch.kind)
        if playlist is None:
            raise RuntimeError("плейлист не найден")
        shorts = list(playlist.tracks or [])
        present = {str(s.id) for s in shorts}
        claimed = {u.track for u in watch.uploads if u.track}

        for upload in watch.uploads:  # по id из ответа загрузки
            if upload.track is None and upload.ugc_ids:
                match = next((s for s in shorts if str(s.id) not in claimed
                              and any(_matches(s, ugc_id) for ugc_id in upload.ugc_ids)), None)
                if match is not None:
                    upload.track = str(match.id)
                    claimed.add(upload.track)
        if watch.known is not None:  # иначе — новые загруженные треки, которых не было до пачки
            fresh = [str(s.id) for s in shorts
                     if str(s.id) not in watch.known and str(s.id) not in claimed and _is_uploaded(s)]
            waiting = [u for u in watch.uploads if u.track is None]
            for upload, track_id in zip(waiting, fresh, strict=False):
                upload.track = track_id

        for upload in watch.uploads:
            if upload.track is not None:
                self._release(upload)  # появился — файл больше не нужен
        found = [u for u in watch.uploads if u.track and not u.placed]
        if not found:
            await self._resend_missing(watch)
            return
        order = sorted((u for u in watch.uploads if u.track in present), key=lambda u: (-u.group, u.seq))
        await watch.ym.move_to_top(watch.kind, [u.track for u in order], playlist)
        for upload in found:
            upload.placed = True
            if not upload.done.done():
                upload.done.set_result(True)
        log.info("Загруженные треки подняты в начало плейлиста %s: %s", watch.kind, [u.track for u in found])

    async def close(self) -> None:
        tasks = [w.task for w in self._watches.values() if w.task is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
