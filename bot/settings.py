"""Настройки пользователя, которые хранит сервер: качество скачивания и прослушивания."""

from __future__ import annotations

from bot.config import Config
from bot.storage import Storage

# Какие MP3 бывают у Яндекса. Выше 320 kbps MP3 не отдаёт; без Плюса доступны только отрывки.
QUALITIES = (320, 192, 128, 64)
QUALITY_LABELS = {320: "лучшее", 192: "хорошее", 128: "экономное", 64: "минимум"}
KINDS = ("download", "stream")  # скачивание и отправка в чат / прослушивание в плеере


def available_qualities(config: Config) -> list[int]:
    """Варианты, которые разрешает владелец сервера (MAX_BITRATE)."""
    return [q for q in QUALITIES if q <= config.max_bitrate]


def get_quality(store: Storage, config: Config, user_id: int, kind: str) -> int:
    """Выбранное качество (kbps) — не выше MAX_BITRATE; по умолчанию лучшее."""
    raw = store.get_setting(user_id, f"{kind}_quality")
    value = int(raw) if raw and raw.isdigit() else config.max_bitrate
    return min(value, config.max_bitrate)


def set_quality(store: Storage, config: Config, user_id: int, kind: str, value: int) -> int:
    if kind not in KINDS:
        raise ValueError(f"Неизвестная настройка качества: {kind}")
    if value not in available_qualities(config):
        allowed = ", ".join(map(str, available_qualities(config)))
        raise ValueError(f"Качество должно быть одним из: {allowed} kbps")
    store.set_setting(user_id, f"{kind}_quality", str(value))
    return value
