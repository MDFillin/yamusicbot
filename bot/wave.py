"""«Моя волна» в мини-приложении: настройки как в приложении Яндекса и выбор пользователя.

Список настроек строится из ответов Яндекса (rotor/wave/settings и rotor/stations/list), поэтому названия
и картинки — те же, что в приложении, а новые занятия или жанры появятся сами. Выбор — по одному значению
в группе — превращается в «зёрна» (seeds) сессии волны: user:onyourwave плюс выбранные, например
activity:workout, genre:rap, settingMoodEnergy:active.
"""

from __future__ import annotations

import json
from typing import Any

from bot.storage import Storage
from bot.ym import cover_url

BASE_SEED = "user:onyourwave"
SETTING_KEY = "wave"
# Группы в порядке показа: (ключ, заголовок). Занятия, жанры и эпохи — станции; остальное — общие настройки.
GROUPS = (
    ("context", "Под занятие"),
    ("diversity", "По характеру"),
    ("moodEnergy", "Под настроение"),
    ("language", "По языку"),
    ("genre", "Жанр"),
    ("epoch", "Эпоха"),
)
SETTING_GROUPS = ("diversity", "moodEnergy", "language")


def _station_item(station: dict[str, Any]) -> dict[str, Any] | None:
    sid = station.get("id") or {}
    if not sid.get("type") or not sid.get("tag") or not station.get("name"):
        return None
    icon = station.get("icon") or {}
    return {
        "seed": f"{sid['type']}:{sid['tag']}",
        "name": station["name"],
        "image": cover_url(icon.get("imageUrl") or icon.get("image_url"), "100x100"),
        "color": icon.get("backgroundColor") or icon.get("background_color"),
    }


def _setting_items(setting: dict[str, Any]) -> list[dict[str, Any]]:
    """Значения настройки без «Любое»: «Любое» — это когда в группе ничего не выбрано."""
    items = []
    for v in setting.get("possibleValues") or setting.get("possible_values") or []:
        seed = v.get("serializedSeed") or v.get("serialized_seed")
        if seed and v.get("name") and not v.get("unspecified"):
            items.append({"seed": seed, "name": v["name"], "image": v.get("imageUrl") or v.get("image_url")})
    return items


def build_options(settings: Any, stations: Any) -> list[dict[str, Any]]:
    """Группы настроек для интерфейса: [{key, title, items: [{seed, name, image, color}]}]."""
    groups: dict[str, list[dict[str, Any]]] = {key: [] for key, _ in GROUPS}
    settings = settings if isinstance(settings, dict) else {}
    for block in settings.get("blocks") or []:
        if block.get("type") == "contexts":
            groups["context"] += [i for i in map(_station_item, block.get("items") or []) if i]
    restrictions = settings.get("settingRestrictions") or settings.get("setting_restrictions") or {}
    for key in SETTING_GROUPS:
        if isinstance(restrictions.get(key), dict):
            groups[key] = _setting_items(restrictions[key])
    for entry in stations if isinstance(stations, list) else []:
        station = entry.get("station") if isinstance(entry, dict) else None
        if not station or station.get("parentId") or station.get("parent_id"):
            continue  # поджанры не показываем: их больше сотни
        kind = (station.get("id") or {}).get("type")
        if kind in ("genre", "epoch") and (item := _station_item(station)):
            groups[kind].append(item)
    return [{"key": key, "title": title, "items": groups[key]} for key, title in GROUPS if groups[key]]


def clean_choice(choice: Any, options: list[dict[str, Any]]) -> dict[str, str]:
    """Только настоящие значения, по одному на группу: чужие seeds в запрос к Яндексу не попадут."""
    if not isinstance(choice, dict):
        return {}
    allowed = {g["key"]: {i["seed"] for i in g["items"]} for g in options}
    return {k: v for k, v in choice.items() if isinstance(v, str) and v in allowed.get(k, ())}


def seeds(choice: dict[str, str]) -> list[str]:
    return [BASE_SEED, *(choice[key] for key, _ in GROUPS if key in choice)]


def load_choice(store: Storage, user_id: int) -> dict[str, str]:
    try:
        value = json.loads(store.get_setting(user_id, SETTING_KEY) or "{}")
    except ValueError:
        return {}
    if not isinstance(value, dict):
        return {}
    return {k: v for k, v in value.items() if isinstance(k, str) and isinstance(v, str)}


def save_choice(store: Storage, user_id: int, choice: dict[str, str]) -> None:
    store.set_setting(user_id, SETTING_KEY, json.dumps(choice, ensure_ascii=False) if choice else None)
