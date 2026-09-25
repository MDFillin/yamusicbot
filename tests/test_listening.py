"""Статистика прослушиваний: разбор истории Яндекса, периоды, расписание итогов и подсчёт."""

from datetime import date, datetime
from types import SimpleNamespace as NS

import pytest

from bot.listening import Listening, current_streak, due_report, offset_of, parse_history, period_at
from bot.storage import Storage


def track(tid, title, artists, album=(10, "Альбом", "rusrock"), duration=180_000):
    return NS(id=tid, title=title, version=None, duration_ms=duration, cover_uri="avatars.yandex.net/x/%%",
              artists=[NS(id=a_id, name=name) for a_id, name in artists],
              albums=[NS(id=album[0], title=album[1], genre=album[2])])


TRACKS = {
    "1": track(1, "Кукушка", [(7, "Кино")]),
    "2": track(2, "Группа крови", [(7, "Кино")]),
    "3": track(3, "Хочешь?", [(8, "Земфира")], album=(20, "ПММЛ", "rusrock")),
    "4": track(4, "Take On Me", [(9, "a-ha")], album=(30, "Hunting High and Low", "pop"), duration=240_000),
}


def item(tid, full=False):
    return NS(type="track", data=NS(item_id=NS(track_id=tid, album_id="10"), full_model=TRACKS[tid] if full else None))


def ctx(type_, **ids):
    return NS(type=type_, data=NS(item_id=NS(**ids), full_model=None))


def history(days):
    """days: {"2026-09-21": [(context, [track ids])]}"""
    return NS(history_tabs=[NS(date=d, items=[NS(context=c, tracks=[item(t) for t in ts]) for c, ts in groups])
                            for d, groups in days.items()])


WAVE = ctx("wave", seeds=["user:onyourwave"])
PLAYLIST = ctx("playlist", uid=42, kind=1003)
ALBUM = ctx("album", id="20")


class FakeYM:
    uid = 42

    def __init__(self, days):
        self.days = days
        self.fetched = []

    async def music_history(self):
        return history(self.days)

    async def get_tracks(self, ids):
        self.fetched.append(list(ids))
        return [TRACKS[i] for i in ids if i in TRACKS]

    async def get_playlist(self, kind, owner=None):
        return NS(title="Мои записи")


class FakeHealth:
    def __init__(self):
        self.ok, self.failures = 0, []

    async def succeeded(self):
        self.ok += 1

    async def failed(self, user_id, details):
        self.failures.append(details)


@pytest.fixture
def listening(tmp_path):
    store = Storage(tmp_path / "bot.db")
    admin = NS(history_health=FakeHealth(), check=lambda uid: None, mark_blocked=lambda uid: None)
    yield Listening(store, accounts=None, admin=admin, bot=None, config=NS(webapp_url=None))
    store.close()


def test_parse_history():
    listens = parse_history(history({"2026-09-21": [(WAVE, ["1", "2"]), (PLAYLIST, ["3"])],
                                     "2026-09-22": [(ALBUM, ["3"]), (None, ["4"])]}))
    assert [(x.day, x.track_id, x.context, x.context_type) for x in listens] == [
        ("2026-09-21", "1", "wave:user:onyourwave", "wave"), ("2026-09-21", "2", "wave:user:onyourwave", "wave"),
        ("2026-09-21", "3", "playlist:42:1003", "playlist"), ("2026-09-22", "3", "album:20", "album"),
        ("2026-09-22", "4", "none", "other")]
    assert parse_history(NS(history_tabs=None)) == [] and parse_history(None) == []


def test_periods():
    d = date(2026, 9, 25)  # пятница
    assert (period_at("week", d).start, period_at("week", d).end) == (date(2026, 9, 21), date(2026, 9, 27))
    assert period_at("week", d).title == "21–27 сентября"
    assert period_at("week", d, 1).title == "28 сентября – 4 октября"
    assert period_at("month", d, -9).title == "Декабрь 2025"
    assert period_at("month", date(2026, 12, 31), 1).start == date(2027, 1, 1)
    assert period_at("day", d, -1).title == "24 сентября" and period_at("year", d).title == "2026 год"
    for kind in ("day", "week", "month", "year"):
        assert offset_of(period_at(kind, d, -3), d) == -3


@pytest.mark.parametrize(("kind", "now", "expected"), [
    ("day", datetime(2026, 9, 25, 21, 59), None),
    ("day", datetime(2026, 9, 25, 22, 0), "day:2026-09-25"),
    ("day", datetime(2026, 9, 26, 2, 0), "day:2026-09-25"),  # бот был выключен вечером — итоги всё равно придут
    ("day", datetime(2026, 9, 26, 5, 0), None),  # но не спустя полдня
    ("week", datetime(2026, 9, 27, 20, 0), None),
    ("week", datetime(2026, 9, 27, 21, 5), "week:2026-09-21"),
    ("week", datetime(2026, 9, 28, 9, 0), "week:2026-09-21"),
    ("week", datetime(2026, 9, 29, 9, 0), None),
    ("month", datetime(2026, 10, 1, 9, 0), None),
    ("month", datetime(2026, 10, 1, 10, 0), "month:2026-09-01"),  # 1-го — итоги прошлого месяца
    ("month", datetime(2026, 10, 2, 20, 0), "month:2026-09-01"),
    ("month", datetime(2026, 10, 4, 0, 0), None),
])
def test_due_report(kind, now, expected):
    p = due_report(kind, now)
    assert (p.key if p else None) == expected


def test_streak():
    days = ["2026-09-20", "2026-09-22", "2026-09-23", "2026-09-24"]
    assert current_streak(days, date(2026, 9, 24)) == 3
    assert current_streak(days, date(2026, 9, 25)) == 3, "сегодня ещё не слушал — серия не прервана"
    assert current_streak(days, date(2026, 9, 26)) == 0


async def test_sync_and_stats(listening, monkeypatch):
    monkeypatch.setattr("bot.listening.msk_now", lambda: datetime(2026, 9, 27, 12, 0))
    ym = FakeYM({"2026-09-14": [(WAVE, ["4"])],
                 "2026-09-21": [(WAVE, ["1", "2"]), (PLAYLIST, ["3", "1"])],
                 "2026-09-22": [(ALBUM, ["3"]), (WAVE, ["1"])]})
    assert await listening.sync(1, ym) == 7  # записи (день, трек, источник)
    assert await listening.sync(1, ym) == 0, "повторная выгрузка ничего не дублирует"
    assert ym.fetched == [["1", "2", "3", "4"]], "данные треков запрашиваются один раз"
    assert listening.admin.history_health.ok == 2

    week = period_at("week", date(2026, 9, 27))
    st = listening.stats(1, week)
    # 21-го «Кукушка» играла и в волне, и в плейлисте — это одно прослушивание за день.
    assert st["plays"] == 5 and st["tracks"] == 3 and st["artists"] == 2 and st["minutes"] == 15
    assert st["active_days"] == 2 and st["days"] == 7 and st["prev_plays"] == 1 and st["change_pct"] == 400
    assert [(a["name"], a["plays"]) for a in st["top_artists"]] == [("Кино", 3), ("Земфира", 2)]
    assert st["top_tracks"][0] == {"id": "1", "plays": 2}
    assert [g["label"] for g in st["genres"]] == ["русский рок"]
    # Источник у прослушивания один: «Кукушку» 21-го засчитали один раз, а не и волне, и плейлисту.
    assert {x["type"]: x["plays"] for x in st["sources"]} == {"wave": 2, "playlist": 2, "album": 1}
    assert {s["title"] for s in st["top_sources"]} == {"Моя волна", "Мои записи", "ПММЛ"}
    assert not st["collecting"] and st["new_tracks"] == 3, "a-ha слушали раньше — это не открытие"
    assert [a["name"] for a in st["new_artists"]] == ["Кино", "Земфира"]
    assert [d["plays"] for d in st["series"]] == [3, 2, 0, 0, 0, 0, 0]

    assert st["estimated"] == 5, "всё из истории: время по длительности"

    text = listening.report_text(1, week)
    assert "Твоя неделя в музыке</b> · 21–27 сентября" in text and "<b>5</b> прослушиваний · ≈ 15 мин" in text
    assert "1. Кино — 3" in text and "1. Кукушка — Кино · 2 раза" in text and "Моя волна 40%" in text
    assert "▲ 400% к прошлой неделе" in text and "<a " not in text, "без имени бота — без ссылок"
    linked = listening.report_text(1, week, username="testbot")
    assert '<a href="https://t.me/testbot?start=t1">Кукушка</a> — <a href="https://t.me/testbot?start=ar7">Кино</a>' \
        in linked


async def test_first_period_is_marked_as_collecting(listening, monkeypatch):
    monkeypatch.setattr("bot.listening.msk_now", lambda: datetime(2026, 9, 27, 12, 0))
    await listening.sync(1, FakeYM({"2026-09-21": [(WAVE, ["1"])]}))
    st = listening.stats(1, period_at("week", date(2026, 9, 27)))
    assert st["collecting"] and st["new_tracks"] is None
    assert "только начала собираться" in listening.report_text(1, period_at("week", date(2026, 9, 27)))


async def test_changed_api_is_reported(listening):
    class Broken(FakeYM):
        async def music_history(self):
            raise KeyError("historyTabs")

    assert await listening.sync(1, Broken({})) is None
    assert listening.admin.history_health.failures == ["KeyError: 'historyTabs'"]


async def test_revoked_login_is_retried_hourly_not_every_tick(listening):
    from yandex_music.exceptions import UnauthorizedError

    calls = []

    class Revoked(FakeYM):
        async def music_history(self):
            calls.append(1)
            raise UnauthorizedError("token revoked")

    async def get(user_id):
        return Revoked({})

    listening.accounts = NS(get=get)
    listening.enable(1)
    now = datetime(2026, 9, 23, 12, 0)
    await listening.tick(now)
    await listening.tick(now)
    assert calls == [1]  # вторая итерация цикла (через 5 минут) Яндекс не дёргает
    assert listening.admin.history_health.failures == []  # отозванный вход — не поломка API


async def test_missing_track_is_not_refetched(listening):
    ym = FakeYM({"2026-09-21": [(WAVE, ["999"])]})
    await listening.sync(1, ym)
    await listening.sync(1, ym)
    assert ym.fetched == [["999"]]
    assert listening.stats(1, period_at("week", date(2026, 9, 21)))["plays"] == 1


def test_settings(listening):
    assert not listening.enabled(1)
    listening.enable(1)
    assert listening.enabled(1) and listening.prefs(1) == {"day": False, "week": True, "month": True}
    assert listening.set_pref(1, "day", True)["day"] is True
    assert listening.store.stats_users() == [1]
    listening.store.add_listens(1, [("2026-09-21", "1", "none")])
    assert listening.disable(1, delete=True) == 1 and not listening.enabled(1)
