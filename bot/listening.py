"""Статистика прослушиваний: история Яндекс Музыки → база бота → итоги дня, недели, месяца и года.

История прослушивания Яндекса (раздел «История» в приложении) общая для всех устройств: приложения, сайта,
колонок. В ней есть день, трек и откуда он играл, но нет времени и числа повторов. Поэтому «прослушивание»
здесь — трек в конкретный день, а время — оценка по длительности треков. Яндекс хранит историю недолго,
бот копит её сам с момента, когда человек включил статистику (раз в час забирает свежую).
"""

from __future__ import annotations

import asyncio
import contextlib
import html
import logging
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from yandex_music.exceptions import NetworkError, TimedOutError, UnauthorizedError

from bot.accounts import Accounts
from bot.admin import DAY_OFFSET, Admin
from bot.callbacks import StatsCb, StatsSetCb
from bot.config import Config
from bot.errors import log_failure, spawn
from bot.keyboards import plural
from bot.storage import Storage
from bot.ym import YandexMusic, YandexNotReady

log = logging.getLogger(__name__)

SYNC_INTERVAL = 3600  # сек: как часто забирать историю у каждого, кто включил статистику
LOOP_INTERVAL = 300
MANUAL_SYNC_INTERVAL = 60  # «Обновить сейчас» — не чаще раза в минуту
# Когда присылать итоги (по Москве) и сколько после этого ещё можно прислать, если бот был выключен.
REPORTS = {
    "day": {"at": (22, 0), "window": timedelta(hours=6)},
    "week": {"weekday": 6, "at": (21, 0), "window": timedelta(hours=24)},  # воскресенье
    "month": {"monthday": 1, "at": (10, 0), "window": timedelta(hours=48)},  # 1-е число, за прошлый месяц
}
DEFAULT_PREFS = {"day": False, "week": True, "month": True}

MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября",
          "ноября", "декабря"]
MONTHS_NOM = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август", "Сентябрь", "Октябрь",
              "Ноябрь", "Декабрь"]
MONTHS_SHORT = ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]
SOURCE_LABELS = {"wave": "Моя волна", "playlist": "Плейлисты", "album": "Альбомы", "artist": "Исполнители",
                 "track": "Треки", "other": "Другое"}
GENRES = {
    "pop": "поп", "ruspop": "русская поп-музыка", "rusestrada": "эстрада", "estrada": "эстрада", "rock": "рок",
    "rusrock": "русский рок", "alternative": "альтернатива", "indie": "инди", "local-indie": "инди",
    "rusindie": "русское инди", "rap": "рэп и хип-хоп", "rusrap": "русский рэп", "foreignrap": "зарубежный рэп",
    "hiphop": "хип-хоп", "phonk": "фонк", "electronics": "электроника", "dance": "танцевальная", "house": "хаус",
    "techno": "техно", "trance": "транс", "dnb": "драм-н-бейс", "dubstep": "дабстеп", "edmgenre": "EDM",
    "metal": "метал", "numetal": "ню-метал", "metalcore": "металкор", "alternativemetal": "альтернативный метал",
    "punk": "панк", "posthardcore": "пост-хардкор", "hardcore": "хардкор", "jazz": "джаз", "blues": "блюз",
    "classical": "классика", "classicalmusic": "классика", "soundtrack": "саундтреки", "films": "из фильмов",
    "videogame": "из игр", "anime": "аниме", "rnb": "R&B", "soul": "соул", "funk": "фанк", "disco": "диско",
    "folk": "фолк", "rusfolk": "русский фолк", "reggae": "регги", "country": "кантри", "shanson": "шансон",
    "bard": "авторская песня", "relax": "для отдыха", "ambient": "эмбиент", "lounge": "лаунж", "kpop": "K-pop",
    "jpop": "J-pop", "latinfolk": "латина", "experimental": "экспериментальная", "newage": "нью-эйдж",
    "children": "детская", "vocal": "вокал", "triphop": "трип-хоп", "synthwave": "синтвейв", "lofi": "lo-fi",
}


def genre_label(genre_id: str) -> str:
    return GENRES.get(genre_id, genre_id.replace("-", " "))


# ---------- периоды ----------

def msk_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=DAY_OFFSET)


@dataclass(frozen=True)
class Period:
    kind: str  # day | week | month | year
    start: date
    end: date

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.start.isoformat()}"

    @property
    def title(self) -> str:
        s, e = self.start, self.end
        if self.kind == "day":
            return f"{s.day} {MONTHS[s.month - 1]}"
        if self.kind == "week":
            if s.month == e.month:
                return f"{s.day}–{e.day} {MONTHS[e.month - 1]}"
            return f"{s.day} {MONTHS[s.month - 1]} – {e.day} {MONTHS[e.month - 1]}"
        if self.kind == "month":
            return f"{MONTHS_NOM[s.month - 1]} {s.year}"
        return f"{s.year} год"

    def days(self) -> list[date]:
        return [self.start + timedelta(days=i) for i in range((self.end - self.start).days + 1)]

    def shifted(self, delta: int) -> Period:
        return period_at(self.kind, self.start, delta)


def period_at(kind: str, base: date, offset: int = 0) -> Period:
    """Период вида kind, в который попадает base, сдвинутый на offset периодов (-1 — предыдущий)."""
    if kind == "day":
        d = base + timedelta(days=offset)
        return Period(kind, d, d)
    if kind == "week":
        start = base - timedelta(days=base.weekday()) + timedelta(weeks=offset)
        return Period(kind, start, start + timedelta(days=6))
    if kind == "month":
        month = base.year * 12 + base.month - 1 + offset
        start = date(month // 12, month % 12 + 1, 1)
        nxt = date((month + 1) // 12, (month + 1) % 12 + 1, 1)
        return Period(kind, start, nxt - timedelta(days=1))
    if kind == "year":
        year = base.year + offset
        return Period(kind, date(year, 1, 1), date(year, 12, 31))
    raise ValueError(f"Неизвестный период {kind}")


def offset_of(p: Period, today: date) -> int:
    """На сколько периодов p отстоит от текущего (0 — текущий, -1 — прошлый)."""
    current = period_at(p.kind, today)
    if p.kind == "day":
        return (p.start - current.start).days
    if p.kind == "week":
        return (p.start - current.start).days // 7
    if p.kind == "month":
        return (p.start.year - current.start.year) * 12 + p.start.month - current.start.month
    return p.start.year - current.start.year


def due_report(kind: str, now: datetime) -> Period | None:
    """Какой период пора подвести итогом (или None). now — московское время без часового пояса."""
    rule = REPORTS[kind]
    hour, minute = rule["at"]
    slot = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if kind == "day":
        if slot > now:
            slot -= timedelta(days=1)
        target = period_at("day", slot.date())
    elif kind == "week":
        slot -= timedelta(days=(slot.weekday() - rule["weekday"]) % 7)
        if slot > now:
            slot -= timedelta(weeks=1)
        target = period_at("week", slot.date())
    else:
        slot = slot.replace(day=rule["monthday"])
        if slot > now:
            prev = period_at("month", slot.date(), -1)
            slot = slot.replace(year=prev.start.year, month=prev.start.month)
        target = period_at("month", slot.date(), -1)  # 1-го числа — итоги прошлого месяца
    return target if now - slot <= rule["window"] else None


# ---------- разбор истории Яндекса ----------

@dataclass
class Listen:
    day: str
    track_id: str
    context: str
    context_type: str
    track: Any = None  # полная модель трека, если Яндекс её прислал


def _context(item: Any) -> tuple[str, str]:
    """Ключ и тип источника из элемента истории: альбом, плейлист, исполнитель, волна."""
    if item is None:
        return "none", "other"
    type_ = str(getattr(item, "type", "") or "other")
    item_id = getattr(getattr(item, "data", None), "item_id", None)
    if item_id is None:
        return "none", "other"
    if type_ == "playlist" and getattr(item_id, "kind", None) is not None:
        return f"playlist:{item_id.uid}:{item_id.kind}", "playlist"
    if type_ == "wave":
        seeds = getattr(item_id, "seeds", None) or []
        return "wave:" + ",".join(map(str, seeds)), "wave"
    if type_ in ("album", "artist") and getattr(item_id, "id", None):
        return f"{type_}:{item_id.id}", type_
    return "none", "other"


def parse_history(history: Any) -> list[Listen]:
    listens: list[Listen] = []
    for tab in getattr(history, "history_tabs", None) or []:
        day = str(getattr(tab, "date", "") or "")[:10]
        if len(day) != 10:
            continue
        for group in getattr(tab, "items", None) or []:
            context, context_type = _context(getattr(group, "context", None))
            for item in getattr(group, "tracks", None) or []:
                data = getattr(item, "data", None)
                item_id = getattr(data, "item_id", None)
                track_id = getattr(item_id, "track_id", None) if item_id is not None else None
                if not track_id:
                    continue
                listens.append(Listen(day, str(track_id), context, context_type, getattr(data, "full_model", None)))
    return listens


def track_meta(track: Any) -> tuple:
    album = track.albums[0] if getattr(track, "albums", None) else None
    title = track.title or "Без названия"
    if getattr(track, "version", None):
        title += f" ({track.version})"
    artists = [(str(a.id), a.name) for a in getattr(track, "artists", None) or [] if getattr(a, "id", None)]
    return (str(track.id), title, str(album.id) if album and album.id else None, album.title if album else None,
            getattr(album, "genre", None) if album else None, getattr(track, "duration_ms", None),
            getattr(track, "cover_uri", None), artists)


# ---------- подсчёт ----------

def fmt_minutes(minutes: int) -> str:
    hours, mins = divmod(int(minutes), 60)
    if not hours:
        return f"{mins} мин"
    return f"{hours} ч {mins} мин" if mins else f"{hours} ч"


def current_streak(days: Iterable[str], today: date) -> int:
    """Сколько дней подряд с музыкой — заканчивая сегодня (или вчера, если сегодня ещё не слушал)."""
    have = set(days)
    d = today if today.isoformat() in have else today - timedelta(days=1)
    streak = 0
    while d.isoformat() in have:
        streak += 1
        d -= timedelta(days=1)
    return streak


class Listening:
    def __init__(self, store: Storage, accounts: Accounts, admin: Admin, bot: Bot, config: Config) -> None:
        self.store, self.accounts, self.admin, self.bot, self.config = store, accounts, admin, bot, config
        self._synced: dict[int, float] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        self._task: asyncio.Task | None = None
        self._jobs: set[asyncio.Task] = set()

    # ---------- настройки ----------

    def enabled(self, user_id: int) -> bool:
        return self.store.get_setting(user_id, "stats") == "1"

    def prefs(self, user_id: int) -> dict[str, bool]:
        result = {}
        for kind, default in DEFAULT_PREFS.items():
            value = self.store.get_setting(user_id, f"stats_{kind}")
            result[kind] = default if value is None else value == "1"
        return result

    def set_pref(self, user_id: int, kind: str, value: bool) -> dict[str, bool]:
        if kind not in DEFAULT_PREFS:
            raise ValueError("Неизвестная настройка")
        self.store.set_setting(user_id, f"stats_{kind}", "1" if value else "0")
        return self.prefs(user_id)

    def enable(self, user_id: int) -> None:
        self.store.set_setting(user_id, "stats", "1")

    def disable(self, user_id: int, delete: bool = False) -> int:
        self.store.set_setting(user_id, "stats", None)
        return self.store.delete_listens(user_id) if delete else 0

    def can_sync_now(self, user_id: int) -> bool:
        return time.monotonic() - self._synced.get(user_id, -1e9) >= MANUAL_SYNC_INTERVAL

    def sync_soon(self, user_id: int) -> None:
        """Первая выгрузка сразу после включения — в фоне."""
        spawn(self.sync(user_id), f"статистика {user_id}", self._jobs)

    # ---------- сбор ----------

    async def sync(self, user_id: int, ym: YandexMusic | None = None) -> int | None:
        """Забирает историю у Яндекса. Возвращает, сколько новых прослушиваний, или None, если не вышло."""
        lock = self._locks.setdefault(user_id, asyncio.Lock())
        async with lock:
            # Время попытки, а не успеха: если вход отозван или Яндекс недоступен, фоновый цикл
            # не будет стучаться каждые 5 минут, а повторит через час.
            self._synced[user_id] = time.monotonic()
            try:
                ym = ym or await self.accounts.get(user_id)
            except YandexNotReady:
                return None
            if ym is None:
                return None
            try:
                history = await ym.music_history()
                listens = parse_history(history)
            except (UnauthorizedError, NetworkError, TimedOutError) as e:  # вход отозван или нет связи
                log.warning("Статистика %s: история недоступна: %r", user_id, e)
                return None
            except Exception as e:  # ответ не такой, как раньше: скорее всего Яндекс изменил API
                log_failure(log, "Статистика %s: не удалось разобрать историю", user_id, exc=e)
                await self.admin.history_health.failed(user_id, f"{type(e).__name__}: {e}"[:400])
                return None
            await self.admin.history_health.succeeded()
            if not listens:
                return 0
            await self._fill_meta(ym, listens)
            added = self.store.add_listens(user_id, [(x.day, x.track_id, x.context) for x in listens])
            await self._fill_contexts(ym, listens)
            if added:
                log.info("Статистика %s: +%s прослушиваний", user_id, added)
            return added

    async def _fill_meta(self, ym: YandexMusic, listens: list[Listen]) -> None:
        ids = sorted({x.track_id for x in listens})
        missing = [i for i in ids if i not in self.store.known_tracks(ids)]
        if not missing:
            return
        full = {x.track_id: x.track for x in listens if x.track is not None and getattr(x.track, "id", None)}
        fetch = [i for i in missing if i not in full]
        if fetch:
            try:
                for track in await ym.get_tracks(fetch):
                    full[str(track.id)] = track
            except Exception as e:
                log_failure(log, "Статистика: не удалось получить данные треков", exc=e)
        for track_id in missing:
            track = full.get(track_id)
            if track is not None:
                self.store.save_track_meta(*track_meta(track))
            else:  # трек удалён или недоступен — запоминаем, чтобы не спрашивать каждый час
                self.store.save_track_meta(track_id, None, None, None, None, None, None, [])

    async def _fill_contexts(self, ym: YandexMusic, listens: list[Listen]) -> None:
        types = {x.context: x.context_type for x in listens if x.context != "none"}
        for key in self.store.unknown_contexts(sorted(types)):
            type_, title = types[key], None
            parts = key.split(":")
            if type_ == "wave":
                title = "Моя волна" if "onyourwave" in key else "Волна"
            elif type_ == "playlist" and len(parts) == 3:
                with contextlib.suppress(Exception):
                    playlist = await ym.get_playlist(parts[2], parts[1])
                    title = playlist.title if playlist else None
            elif type_ == "album":
                title = self.store.album_title(parts[1])
            elif type_ == "artist":
                title = self.store.artist_name(parts[1])
            self.store.save_context(key, type_, title)

    # ---------- статистика ----------

    def stats(self, user_id: int, p: Period) -> dict[str, Any]:
        raw = self.store.listening_stats(user_id, p.start.isoformat(), p.end.isoformat())
        prev = self.store.listening_stats(user_id, *(d.isoformat() for d in (p.shifted(-1).start, p.shifted(-1).end)),
                                          top=0)
        since = self.store.listening_since(user_id)
        today = msk_now().date()
        # Первый период после включения: «новое» тогда — вообще всё, считать открытия рано.
        collecting = since is None or since >= p.start.isoformat()
        plays = raw["plays"]
        genres_total = sum(g["plays"] for g in raw["genres"]) or 1
        sources_total = sum(x["plays"] for x in raw["sources"]) or 1
        if p.kind == "year":
            months: dict[str, int] = {}
            for day, n in raw["by_day"].items():
                months[day[:7]] = months.get(day[:7], 0) + n
            series = [{"day": f"{p.start.year}-{m:02d}-01", "label": MONTHS_SHORT[m - 1],
                       "plays": months.get(f"{p.start.year}-{m:02d}", 0)} for m in range(1, 13)]
        else:
            series = [{"day": d.isoformat(), "plays": raw["by_day"].get(d.isoformat(), 0)} for d in p.days()]
        return {
            "plays": plays,
            "minutes": raw["minutes"],
            "tracks": raw["tracks"],
            "artists": raw["artists"],
            "active_days": len(raw["by_day"]),
            "days": len([d for d in p.days() if d <= today]),
            "streak": current_streak(self.store.listening_days(user_id), today),
            "prev_plays": prev["plays"],
            "change_pct": round(100 * (plays - prev["plays"]) / prev["plays"]) if prev["plays"] else None,
            "collecting": collecting,
            "since": since,
            "new_tracks": None if collecting else raw["new_tracks"],
            "new_artists": None if collecting else raw["new_artists"][:5],
            "new_artists_count": None if collecting else len(raw["new_artists"]),
            "series": series,
            "top_artists": [{**a, "pct": round(100 * a["plays"] / plays) if plays else 0} for a in raw["top_artists"]],
            "top_tracks": raw["top_tracks"],
            "top_albums": raw["top_albums"],
            "genres": [{"id": g["id"], "label": genre_label(g["id"]), "plays": g["plays"],
                        "pct": round(100 * g["plays"] / genres_total)} for g in raw["genres"][:6]],
            "sources": [{"type": x["type"], "label": SOURCE_LABELS.get(x["type"], x["type"]), "plays": x["plays"],
                         "pct": round(100 * x["plays"] / sources_total)} for x in raw["sources"]],
            "top_sources": raw["top_sources"],
        }

    def report_text(self, user_id: int, p: Period, heading: str | None = None) -> str:
        st = self.stats(user_id, p)
        heads = {"day": "Твой день в музыке", "week": "Твоя неделя в музыке", "month": "Твой месяц в музыке",
                 "year": "Твой год в музыке"}
        lines = [f"📊 <b>{heading or heads[p.kind]}</b> · {p.title}", ""]
        if not st["plays"]:
            lines.append("Пока пусто: слушайте музыку в Яндекс Музыке — в приложении, на сайте или на колонке, "
                         "а я посчитаю. История обновляется раз в час.")
            return "\n".join(lines)
        change = ""
        if st["change_pct"] is not None and p.kind != "day":
            arrow = "▲" if st["change_pct"] >= 0 else "▼"
            what = {"week": "прошлой неделе", "month": "прошлому месяцу", "year": "прошлому году"}[p.kind]
            change = f" ({arrow} {abs(st['change_pct'])}% к {what})"
        lines.append(f"🎧 <b>{st['plays']}</b> {plural(st['plays'], 'трек', 'трека', 'треков')} · "
                     f"≈ {fmt_minutes(st['minutes'])}{change}")
        artists = f"👤 <b>{st['artists']}</b> {plural(st['artists'], 'исполнитель', 'исполнителя', 'исполнителей')}"
        if st["new_artists_count"]:
            artists += f", из них 🆕 {st['new_artists_count']} новых"
        lines.append(artists)
        if p.kind != "day":
            lines.append(f"📅 Дней с музыкой: {st['active_days']} из {st['days']}")
        if st["streak"] >= 2:
            lines.append(f"🔥 Серия: {st['streak']} {plural(st['streak'], 'день', 'дня', 'дней')} подряд")
        if st["top_artists"]:
            lines += ["", "<b>Топ исполнителей</b>"]
            lines += [f"{i}. {html.escape(a['name'] or '—')} — {a['plays']}"
                      for i, a in enumerate(st["top_artists"][:5], 1)]
        if st["top_tracks"]:
            names = self.store.track_names([t["id"] for t in st["top_tracks"][:5]])
            lines += ["", "<b>Топ треков</b>"]
            for i, t in enumerate(st["top_tracks"][:5], 1):
                title, artists_ = names.get(t["id"], ("Трек", ""))
                label = f"{artists_} — {title}" if artists_ else title
                days = f" ({t['plays']} {plural(t['plays'], 'день', 'дня', 'дней')})" if p.kind != "day" else ""
                lines.append(f"{i}. {html.escape(label)}{days}")
        if st["genres"]:
            lines += ["", "<b>Жанры:</b> " + " · ".join(f"{html.escape(g['label'])} {g['pct']}%"
                                                        for g in st["genres"][:4])]
        if st["sources"]:
            lines.append("<b>Откуда:</b> " + " · ".join(f"{html.escape(x['label'])} {x['pct']}%"
                                                        for x in st["sources"][:4]))
        if st["new_artists"]:
            lines.append("<b>Открытия:</b> " + ", ".join(html.escape(a["name"] or "—") for a in st["new_artists"][:3]))
        if st["collecting"]:
            lines += ["", "<i>Статистика только начала собираться — новые открытия появятся со следующего периода.</i>"]
        return "\n".join(lines)

    def report_markup(self, p: Period, offset: int = 0) -> InlineKeyboardMarkup:
        rows: list[list[InlineKeyboardButton]] = []
        if self.config.webapp_url:
            rows.append([InlineKeyboardButton(text="📊 Подробнее в медиатеке", web_app=WebAppInfo(
                url=f"{self.config.webapp_url}/?stats={p.kind}"))])
        rows.append([InlineKeyboardButton(text=("• " + label + " •") if kind == p.kind else label,
                                          callback_data=StatsCb(kind=kind).pack())
                     for kind, label in (("day", "День"), ("week", "Неделя"), ("month", "Месяц"), ("year", "Год"))])
        def step(text: str, delta: int) -> InlineKeyboardButton:
            return InlineKeyboardButton(text=text, callback_data=StatsCb(kind=p.kind, offset=offset + delta).pack())

        nav = [step("‹ Раньше", -1)]
        if offset < 0:
            nav.append(step("Позже ›", 1))
        rows.append(nav)
        rows.append([InlineKeyboardButton(text="⚙️ Итоги в чат", callback_data=StatsSetCb(key="menu").pack())])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    # ---------- фоновая работа ----------

    def start(self) -> None:
        self._task = spawn(self._run(), "статистика прослушиваний")

    async def close(self) -> None:
        for task in [self._task, *self._jobs]:
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    async def _run(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log_failure(log, "Статистика: сбой фонового цикла", exc=e)
            await asyncio.sleep(LOOP_INTERVAL)

    async def tick(self, now: datetime | None = None) -> None:
        now = now or msk_now()
        for user_id in self.store.stats_users():
            if self.admin.check(user_id) is not None:  # заблокирован или техработы
                continue
            if time.monotonic() - self._synced.get(user_id, -1e9) >= SYNC_INTERVAL:
                await self.sync(user_id)
                await asyncio.sleep(0.2)
            await self.send_due_reports(user_id, now)

    async def send_due_reports(self, user_id: int, now: datetime) -> None:
        prefs = self.prefs(user_id)
        for kind in ("month", "week", "day"):
            if not prefs[kind] or (p := due_report(kind, now)) is None:
                continue
            if self.store.get_setting(user_id, f"stats_sent_{kind}") == p.key:
                continue
            if time.monotonic() - self._synced.get(user_id, -1e9) > 600:
                await self.sync(user_id)  # перед итогами — самые свежие данные
            self.store.set_setting(user_id, f"stats_sent_{kind}", p.key)  # до отправки: не прислать дважды
            if not self.stats(user_id, p)["plays"]:
                continue  # пустые итоги не шлём
            try:
                await self.bot.send_message(user_id, self.report_text(user_id, p),
                                            reply_markup=self.report_markup(p, offset_of(p, now.date())))
            except TelegramForbiddenError:
                self.admin.mark_blocked(user_id)
            except Exception as e:
                log_failure(log, "Статистика: не удалось отправить итоги %s", user_id, exc=e)
