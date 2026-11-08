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


def score(wanted: Wanted, title: str, artists: list[str], duration_ms: int) -> float:
    title_sim = similarity(norm_title(wanted.title), norm_title(title))
    artist_sim = max((similarity(_plain(a), _plain(b)) for a in wanted.artists for b in artists), default=0.0)
    if not wanted.artists:
        artist_sim = 0.5
    s = 0.55 * title_sim + 0.35 * artist_sim
    if wanted.duration_ms and duration_ms:
        diff = abs(wanted.duration_ms - duration_ms) / 1000
        s += 0.10 * max(0.0, 1 - max(0.0, diff - 3) / 15)
    else:
        s += 0.05
    if versions(wanted.title) != versions(title):
        s -= 0.35
    if title_sim < 0.6 or artist_sim < 0.6:
        s = min(s, ACCEPT - 0.01)
    return s


def queries(wanted: Wanted) -> list[str]:
    title = norm_title(wanted.title)
    artist = wanted.artists[0] if wanted.artists else ""
    full = _plain(wanted.title)
    exact = f'track:"{title}" artist:"{_plain(artist)}"' if artist else f'track:"{title}"'
    result = [exact, f"{_plain(artist)} {full}"]
    if latin(full) != full:
        result.append(f"{latin(_plain(artist))} {latin(title)}")
    return list(dict.fromkeys(q.strip() for q in result if q.strip()))


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
                              "total": None, "done": 0, "found": 0, "already": 0, "missed": [], "url": None}
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
        st["title"] = source.name or source.title
        st["total"] = len(source.items)
        self._save(job)
        uris: list[str] = []
        for offset in range(0, len(source.items), YM_PAGE):
            with net.background():
                tracks = await resolve(ym, source.items[offset:offset + YM_PAGE])
            wanted = [Wanted(str(t.id), track_title(t), [a.name for a in t.artists or [] if a.name] or
                             [track_artists(t)], t.duration_ms or 0) for t in tracks]
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
                    uris.append(uri)
                    st["found"] += 1
                elif len(st["missed"]) < MAX_MISSES:
                    st["missed"].append({"title": w.title, "artists": ", ".join(w.artists)})
                st["done"] += 1
                if st["done"] % SAVE_EVERY == 0:
                    self._save(job)
            st["done"] = min(st["total"], offset + YM_PAGE)  # и те, что Яндекс не отдал (удалённые)
        uris = list(dict.fromkeys(uris))  # дубли в плейлисте Spotify не нужны
        if pick.src == "likes":
            # У Яндекса новые лайки первыми; Spotify ставит дату по порядку — добавляем от старых к новым.
            await sp.save_tracks(list(reversed(uris)))
            st["url"] = "https://open.spotify.com/collection/tracks"
        else:
            playlist = await sp.create_playlist(st["title"], "Перенесено из Яндекс Музыки")
            if uris:
                await sp.add_to_playlist(playlist["id"], uris)
            st["url"] = (playlist.get("external_urls") or {}).get("spotify")
        self._save(job)


def summary_text(state: dict) -> str:
    status = state.get("status")
    head = {"done": "✅ Перенос в Spotify закончен", "cancelled": "⏹ Перенос в Spotify остановлен",
            "failed": "⚠️ Перенос в Spotify прервался"}.get(status, "Перенос в Spotify")
    lines = [head]
    for s in state.get("sources") or []:
        if s.get("total") is None:
            continue
        missed = s["total"] - s["found"] if s["done"] >= s["total"] else len(s["missed"])
        lines.append(f"• {s['title']}: перенесено {s['found']} из {s['total']}"
                     + (f", не нашлось {missed}" if missed else ""))
    if state.get("error"):
        lines.append(state["error"])
    lines.append("Подробности и список ненайденного — во вкладке «Перенос» в «Медиатеке».")
    return "\n".join(lines)
