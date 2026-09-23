"""Данные inline-кнопок (Telegram ограничивает callback_data 64 байтами)."""

from aiogram.filters.callback_data import CallbackData


class TrackCb(CallbackData, prefix="t"):
    action: str  # dl | like | unlike | add
    track: str


class ViewCb(CallbackData, prefix="v"):
    """Постраничный список треков. src: likes | pl | alb | art | pls (список плейлистов)."""

    src: str
    ref: str = ""  # pl: "<uid>.<kind>", alb/art: id
    page: int = 0


class BulkCb(CallbackData, prefix="b"):
    """Скачать весь список (альбом, плейлист, лайки, артист)."""

    src: str
    ref: str = ""


class CancelBulkCb(CallbackData, prefix="x"):
    pass


class PlaylistCb(CallbackData, prefix="p"):
    action: str  # target | untarget | delete | delete_ok | insert | new
    kind: int = 0
    track: str = ""


class UploadCb(CallbackData, prefix="u"):
    action: str  # to | new | cancel
    kind: int = 0


class SearchCb(CallbackData, prefix="s"):
    type: str  # track | album | playlist | artist


class NoopCb(CallbackData, prefix="n"):
    pass
