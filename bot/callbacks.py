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
    action: str  # to | pick | new | cancel
    kind: int = 0


class EditCb(CallbackData, prefix="e"):
    """Редактор данных файла из очереди загрузки (pid — номер файла в очереди)."""

    action: str  # open | title | artist | album | year | cover | nocover | done
    pid: int


class SearchCb(CallbackData, prefix="s"):
    type: str  # track | album | playlist | artist


class NoopCb(CallbackData, prefix="n"):
    pass


class SettingsCb(CallbackData, prefix="cfg"):
    """Настройки в чате: kind — download (качество скачивания), value — kbps."""

    kind: str
    value: int


class MenuCb(CallbackData, prefix="m"):
    """Меню и аккаунт.

    action: login | login_cancel | logout | logout_ok | logout_no | account | likes | playlists | target | help
    """

    action: str


class AdminCb(CallbackData, prefix="adm"):
    """Кнопки админ-панели в чате (работают только у админов — см. IsAdmin)."""

    action: str
    user: int = 0
