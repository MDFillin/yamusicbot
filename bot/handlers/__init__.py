from aiogram import Router

from bot.handlers import browse, common, download, upload


def build_router() -> Router:
    root = Router(name="root")
    # Порядок важен: команды -> загрузка (в т.ч. ввод названия плейлиста) -> скачивание -> поиск -> остальное.
    root.include_routers(common.router, upload.router, download.router, browse.router, common.fallback_router)
    return root
