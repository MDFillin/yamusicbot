"""Перенос музыки между сервисами: сначала Яндекс Музыка → Spotify (лайки и плейлисты).

Как ищется пара: в Spotify ищем по названию и исполнителю (несколько запросов, в том числе транслитом),
каждый кандидат получает оценку по похожести названия, исполнителя и длительности; ремикс, live, slowed
и т. п. считаются другим треком. Найденные пары запоминаются (transfer_matches) — повторный перенос и чужие
переносы тех же треков идут без поиска. «Не нашли» перепроверяется через неделю: каталоги пополняются.

Перенос идёт в фоне по одному на человека; прогресс и итог — в transfer_jobs, по окончании бот пишет в чат.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import unicodedata
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from bot import net
from bot.sources import load_source, resolve
from bot.spotify import Spotify, SpotifyError
from bot.storage import Storage
from bot.ym import YandexMusic, track_artists, track_title

log = logging.getLogger(__name__)

YM2SP = "ym2sp"
MISS_RETRY = 7 * 86400  # «не нашли» перепроверяем через неделю
ACCEPT = 0.74  # минимальная оценка пары
GOOD_ENOUGH = 0.92  # такой пары хватает — дальше не ищем
SAVE_EVERY = 10  # сохранять прогресс каждые N треков
MAX_MISSES = 3000  # сколько ненайденных треков хранить в итоге
YM_PAGE = 100

# Пометки версии: если у одного трека она есть, а у другого нет — это разные записи.
VERSION_WORDS = ("remix", "ремикс", "live", "лайв", "acoustic", "акустик", "sped up", "speed up", "slowed",
                 "nightcore", "instrumental", "инструментал", "karaoke", "караоке", "cover", "кавер",
                 "radio edit", "extended", "mashup", "мэшап", "bass boosted", "8d", "reverb")
_FEAT = re.compile(r"\s*[\(\[]?\b(feat\.?|ft\.?|featuring|при уч\.?|при участии)\s[^\)\]]*[\)\]]?", re.I)
_BRACKETS = re.compile(r"[\(\[][^\)\]]*[\)\]]")
_NON_WORD = re.compile(r"[^\w\s]", re.U)
_SPACES = re.compile(r"\s+")
_TRANSLIT = str.maketrans({
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z", "и": "i", "й": "y",
    "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f",
    "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
})


def _plain(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "").lower().replace("ё", "е")
    return _SPACES.sub(" ", _NON_WORD.sub(" ", text)).strip()


def norm_title(title: str) -> str:
    """Название без «feat.» и скобок: «Song (feat. X) [Remastered]» → «song»."""
    return _plain(_BRACKETS.sub(" ", _FEAT.sub(" ", title or ""))) or _plain(title)


def latin(text: str) -> str:
    return text.translate(_TRANSLIT)


def versions(title: str) -> set[str]:
    low = (title or "").lower().replace("ё", "е")
    return {w for w in VERSION_WORDS if re.search(rf"(?<!\w){re.escape(w)}(?!\w)", low)}


def similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    a2, b2 = latin(a), latin(b)  # «Кино» и «Kino» — одно и то же
    best = max(SequenceMatcher(None, a, b).ratio(), SequenceMatcher(None, a2, b2).ratio())
    if (a2 in b2 or b2 in a2) and min(len(a2), len(b2)) >= 4:
        best = max(best, 0.9)
    return best


@dataclass
class Wanted:
    """Что ищем: трек из исходного сервиса."""
    src_id: str
    title: str  # полное название с версией
    artists: list[str]
    duration_ms: int
    ugc: bool = False  # файл, загруженный самим человеком: теги бывают любыми
    pos: int = 0  # место в исходном списке (с 1)


_ARTIST_SPLIT = re.compile(r"\s*(?:\\|/|,|&|;|\s+(?:feat\.?|ft\.?|x|и|and)\s+)\s*", re.I)
_DASH = re.compile(r"\s+[-–—]\s+")


def artist_names(artists: list[str]) -> list[str]:
    """Исполнители и их части: «A\\B», «A & B», «A feat. B» — у файлов так пишут нескольких в одном поле."""
    names = []
    for a in artists:
        names += [a, *_ARTIST_SPLIT.split(a or "")]
    return list(dict.fromkeys(n.strip() for n in names if n and n.strip()))


def variants(wanted: Wanted) -> list[tuple[str, list[str]]]:
    """Как ещё можно прочитать название и исполнителя. У файлов часто «Исполнитель - Название» в названии,
    а в поле исполнителя — тот, кто выложил файл, или лейбл."""
    artists = artist_names(wanted.artists)
    result = [(wanted.title, artists)]
    if wanted.ugc and len(parts := _DASH.split(wanted.title, maxsplit=1)) == 2:
        left, right = parts
        result += [(right, artist_names([left]) + artists), (left, artist_names([right]) + artists)]
    return result


def _score(title: str, artists: list[str], wanted: Wanted, c_title: str, c_artists: list[str], c_ms: int) -> float:
    title_sim = similarity(norm_title(title), norm_title(c_title))
    artist_sim = max((similarity(_plain(a), _plain(b)) for a in artists for b in c_artists), default=0.0)
    if not artists:
        artist_sim = 0.5
    diff = abs(wanted.duration_ms - c_ms) / 1000 if wanted.duration_ms and c_ms else None
    s = 0.55 * title_sim + 0.35 * artist_sim + (0.05 if diff is None else 0.10 * max(0.0, 1 - max(0.0, diff - 3) / 15))
    if versions(wanted.title) != versions(c_title):
        return s - 0.35
    if title_sim < 0.6 or artist_sim < 0.6:
        # Файл с чужим «исполнителем» (лейбл, кто выложил): то же название и длительность до 2 секунд — та же запись.
        if wanted.ugc and title_sim >= 0.9 and diff is not None and diff <= 2:
            return ACCEPT + 0.02
        return min(s, ACCEPT - 0.01)
    return s


def score(wanted: Wanted, title: str, artists: list[str], duration_ms: int) -> float:
    return max(_score(t, a, wanted, title, artists, duration_ms) for t, a in variants(wanted))


def queries(wanted: Wanted) -> list[str]:
    result = []
    for title, artists in variants(wanted):
        t, a = norm_title(title), _plain(artists[0]) if artists else ""
        result += [f'track:"{t}" artist:"{a}"' if a else f'track:"{t}"', f"{a} {_plain(title)}"]
        if latin(t) != t or latin(a) != a:
            result.append(f"{latin(a)} {latin(t)}")
    if wanted.ugc:
        result.append(f'track:"{norm_title(wanted.title)}"')  # исполнитель в файле может быть чужим
    return list(dict.fromkeys(q.strip() for q in result if q.strip()))[:7]


async def find_on_spotify(sp: Spotify, wanted: Wanted) -> str | None:
    """URI лучшей пары в Spotify или None."""
    best_uri, best = None, 0.0
    for q in queries(wanted):
        for t in await sp.search_tracks(q):
            s = score(wanted, t.get("name") or "", [a.get("name") or "" for a in t.get("artists") or []],
                      int(t.get("duration_ms") or 0))
            if s > best and t.get("uri"):
                best_uri, best = t["uri"], s
        if best >= GOOD_ENOUGH:
            break
    return best_uri if best >= ACCEPT else None


def is_uploaded(track: Any) -> bool:
    return not str(track.id).isdigit()  # у загруженных файлов id — UUID, у каталожных — число


# ---------- задания ----------

@dataclass
class SourcePick:
    src: str  # likes | pl
    ref: str = ""


Notify = Callable[[int, str], Awaitable[Any]]


class TransferJob:
    def __init__(self, job_id: int, user_id: int, state: dict) -> None:
        self.id = job_id
        self.user_id = user_id
        self.state = state
        self.cancelled = False
        self.task: asyncio.Task | None = None


class Transfers:
    """Переносы всех пользователей: не больше одного одновременно у каждого."""

    def __init__(self, store: Storage, notify: Notify | None = None) -> None:
        self.store = store
        self.notify = notify
        self.jobs: dict[int, TransferJob] = {}
        if count := store.interrupt_transfer_jobs():
            log.info("Перенос: прервано перезапуском бота — %s", count)

    def running(self, user_id: int) -> TransferJob | None:
        job = self.jobs.get(user_id)
        return job if job and job.task and not job.task.done() else None

    def start(self, user_id: int, ym: YandexMusic, sp: Spotify, picks: list[SourcePick]) -> TransferJob:
        if self.running(user_id):
            raise RuntimeError("Перенос уже идёт")
        state = {"direction": YM2SP, "status": "running", "started_at": int(time.time()), "finished_at": None,
                 "error": None, "current": 0,
                 "sources": [{"src": p.src, "ref": p.ref, "title": "Мне нравится" if p.src == "likes" else "Плейлист",
                              "total": None, "done": 0, "found": 0, "already": 0, "missed": [], "url": None,
                              "phase": "wait"}
                             for p in picks]}
        job = TransferJob(self.store.add_transfer_job(user_id, YM2SP, state), user_id, state)
        self.jobs[user_id] = job
        job.task = asyncio.create_task(self._run(job, ym, sp, picks))
        return job

    def cancel(self, user_id: int) -> bool:
        job = self.running(user_id)
        if job is None:
            return False
        job.cancelled = True
        return True

    def _save(self, job: TransferJob, status: str = "running") -> None:
        job.state["status"] = status
        if status != "running":
            job.state["finished_at"] = int(time.time())
        self.store.update_transfer_job(job.id, status, job.state)

    async def _run(self, job: TransferJob, ym: YandexMusic, sp: Spotify, picks: list[SourcePick]) -> None:
        status = "done"
        try:
            for i, pick in enumerate(picks):
                if job.cancelled:
                    break
                job.state["current"] = i
                await self._source(job, ym, sp, pick, job.state["sources"][i])
            if job.cancelled:
                status = "cancelled"
        except asyncio.CancelledError:  # бот останавливается
            status = "interrupted"
            raise
        except SpotifyError as e:
            status, job.state["error"] = "failed", str(e)
        except Exception:  # итог переноса пользователь должен увидеть в любом случае
            log.exception("Перенос %s упал", job.id)
            status, job.state["error"] = "failed", "Что-то пошло не так — попробуйте ещё раз"
        finally:
            self._save(job, status)
        if self.notify:
            try:
                await self.notify(job.user_id, summary_text(job.state))
            except Exception as e:  # noqa: BLE001
                log.info("Перенос %s: не отправил итог в чат: %s", job.id, e)

    async def _source(self, job: TransferJob, ym: YandexMusic, sp: Spotify, pick: SourcePick, st: dict) -> None:
        with net.background():  # Яндекс читаем без спешки: люди в боте важнее
            source = await load_source(ym, pick.src, pick.ref)
        items = list(source.items)
        likes = pick.src == "likes"
        st["title"] = source.name or source.title
        st["total"] = len(items)
        st["phase"] = "search"
        self._save(job)
        # Порядок как в Яндексе. У Яндекса новые лайки — первыми, а Spotify сортирует «Любимые» по дате
        # добавления: лайки ищем и сохраняем от старых к новым.
        order = list(range(len(items)))
        if likes:
            order.reverse()
        found: list[str] = []
        for chunk_start in range(0, len(order), YM_PAGE):
            chunk = order[chunk_start:chunk_start + YM_PAGE]
            with net.background():
                tracks = {str(t.id): t for t in await resolve(ym, [items[i] for i in chunk])}
            wanted = []
            for i in chunk:
                item = items[i]
                track = tracks.get(item.split(":")[0] if isinstance(item, str) else str(item.id))
                if track is None:  # удалён из каталога — Яндекс его не отдал
                    st["done"] += 1
                    continue
                names = [a.name for a in track.artists or [] if a.name] or [track_artists(track)]
                wanted.append(Wanted(str(track.id), track_title(track), names, track.duration_ms or 0,
                                     ugc=is_uploaded(track), pos=i + 1))
            known = self.store.get_matches(YM2SP, [w.src_id for w in wanted])
            for w in wanted:
                if job.cancelled:
                    return
                hit = known.get(w.src_id)
                if hit and (hit[0] or time.time() - hit[1] < MISS_RETRY):
                    uri = hit[0]
                    st["already"] += 1
                else:
                    uri = await find_on_spotify(sp, w)
                    self.store.set_match(YM2SP, w.src_id, uri)
                if uri:
                    found.append(uri)
                    st["found"] += 1
                    st["files_found"] = st.get("files_found", 0) + w.ugc
                elif len(st["missed"]) < MAX_MISSES:
                    st["missed"].append({"title": w.title, "artists": ", ".join(w.artists), "id": w.src_id,
                                         "ugc": w.ugc, "pos": w.pos})
                st["done"] += 1
                if st["done"] % SAVE_EVERY == 0:
                    self._save(job)
        if likes:
            await self._save_likes(job, sp, list(dict.fromkeys(found)), st)
        else:
            await self._fill_playlist(job, sp, pick, found, st)
        st["phase"] = "done"
        self._save(job)

    async def _save_likes(self, job: TransferJob, sp: Spotify, uris: list[str], st: dict) -> None:
        """По одному, от старых к новым. Что уже в «Любимых» — не трогаем: повторное сохранение могло бы
        сдвинуть дату добавления и сломать порядок."""
        st["phase"] = "saving"
        saved = await sp.library_contains(uris)
        todo = [u for u, already in zip(uris, saved, strict=True) if not already]
        st["to_save"], st["saved"] = len(todo), 0
        self._save(job)

        def on_saved(_uri: str) -> bool:
            st["saved"] += 1
            if st["saved"] % SAVE_EVERY == 0:
                self._save(job)
            return not job.cancelled

        await sp.save_tracks(todo, on_saved)
        st["url"] = "https://open.spotify.com/collection/tracks"

    async def _fill_playlist(self, job: TransferJob, sp: Spotify, pick: SourcePick, uris: list[str], st: dict) -> None:
        """Плейлист в Spotify — с тем же названием и в том же порядке. Повторный перенос обновляет тот же
        плейлист (бот помнит, какой создал), а не плодит копии."""
        if job.cancelled:
            return
        st["phase"] = "saving"
        self._save(job)
        key = f"spotify_playlist:{pick.ref}"
        playlist_id = self.store.get_setting(job.user_id, key)
        if playlist_id:
            try:
                await sp.replace_playlist(playlist_id, uris)
            except SpotifyError as e:
                if e.status not in (403, 404):
                    raise
                playlist_id = None  # удалили в Spotify — создадим заново
        if not playlist_id:
            playlist = await sp.create_playlist(st["title"], "Перенесено из Яндекс Музыки")
            playlist_id = str(playlist["id"])
            self.store.set_setting(job.user_id, key, playlist_id)
            if uris:
                await sp.add_to_playlist(playlist_id, uris)
        st["url"] = f"https://open.spotify.com/playlist/{playlist_id}"


def summary_text(state: dict) -> str:
    status = state.get("status")
    head = {"done": "✅ Перенос в Spotify закончен", "cancelled": "⏹ Перенос в Spotify остановлен",
            "failed": "⚠️ Перенос в Spotify прервался"}.get(status, "Перенос в Spotify")
    lines = [head]
    files = 0
    for s in state.get("sources") or []:
        if s.get("total") is None:
            continue
        missed = s["total"] - s["found"] if s["done"] >= s["total"] else len(s["missed"])
        files += sum(1 for m in s["missed"] if m.get("ugc"))
        lines.append(f"• {s['title']}: перенесено {s['found']} из {s['total']}"
                     + (f", не нашлось {missed}" if missed else ""))
    if files:
        lines.append(f"Ваших загруженных файлов нет в Spotify: {files}. Их можно получить в чат кнопкой во вкладке "
                     "«Перенос» и добавить в Spotify на компьютере как локальные файлы.")
    if state.get("error"):
        lines.append(state["error"])
    lines.append("Подробности и список ненайденного — во вкладке «Перенос» в «Медиатеке».")
    return "\n".join(lines)
