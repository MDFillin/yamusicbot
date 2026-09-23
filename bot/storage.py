"""Небольшое JSON-хранилище: кэш file_id отправленных треков и настройки пользователей."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class Storage:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._data: dict[str, Any] = {"file_ids": {}, "upload_targets": {}}
        if path.exists():
            try:
                self._data.update(json.loads(path.read_text("utf-8")))
            except (OSError, ValueError):
                log.exception("Не удалось прочитать %s, начинаю с пустого хранилища", path)

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=1), "utf-8")
        os.replace(tmp, self._path)

    # Кэш file_id: один раз скачанный трек повторно отправляется мгновенно.
    def get_file_id(self, track_id: str, bitrate: int) -> str | None:
        return self._data["file_ids"].get(f"{track_id}@{bitrate}")

    def set_file_id(self, track_id: str, bitrate: int, file_id: str) -> None:
        self._data["file_ids"][f"{track_id}@{bitrate}"] = file_id
        self._save()

    # Плейлист, куда сразу загружаются присланные файлы.
    def get_upload_target(self, user_id: int) -> int | None:
        return self._data["upload_targets"].get(str(user_id))

    def set_upload_target(self, user_id: int, kind: int | None) -> None:
        if kind is None:
            self._data["upload_targets"].pop(str(user_id), None)
        else:
            self._data["upload_targets"][str(user_id)] = kind
        self._save()
