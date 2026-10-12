from aiogram import Router

from bot.handlers import account, admin, browse, common, download, inline, merge, shared, stats, upload


def build_router() -> Router:
    root = Router(name="root")
    # Порядок важен: команды и меню -> админка (только для ADMIN_IDS) -> вход в Яндекс -> загрузка
    # (в т.ч. ввод названия плейлиста) -> скачивание -> инлайн -> поиск -> остальное.
    root.include_routers(
        shared.join_router, common.router, admin.router, account.router, stats.router, merge.router, shared.router,
        upload.router, download.router, inline.router, browse.router,
        common.fallback_router,
    )
    return root
