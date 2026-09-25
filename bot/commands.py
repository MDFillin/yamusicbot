"""Меню команд бота: общее и расширенное — для админов (показывается только в их личном чате)."""

from aiogram.types import BotCommand

COMMANDS = [
    BotCommand(command="app", description="🎧 Открыть медиатеку"),
    BotCommand(command="likes", description="❤️ Мне нравится"),
    BotCommand(command="playlists", description="📃 Мои плейлисты"),
    BotCommand(command="stats", description="📊 Моя статистика прослушиваний"),
    BotCommand(command="target", description="📌 Плейлист по умолчанию для загрузки"),
    BotCommand(command="newplaylist", description="➕ Создать плейлист"),
    BotCommand(command="menu", description="🏠 Главное меню"),
    BotCommand(command="settings", description="⚙️ Настройки: качество, аккаунт"),
    BotCommand(command="login", description="🔑 Подключить Яндекс Музыку"),
    BotCommand(command="logout", description="🚪 Отключить аккаунт"),
    BotCommand(command="help", description="❓ Справка"),
    BotCommand(command="cancel", description="Отменить действие"),
]
# Видны только админам (в их личном чате с ботом).
ADMIN_COMMANDS = [
    BotCommand(command="admin", description="🛡 Админ-панель"),
    BotCommand(command="user", description="👤 Пользователь: /user ID"),
    BotCommand(command="broadcast", description="📣 Рассылка всем"),
    BotCommand(command="admins", description="👮 Администраторы"),
    *COMMANDS,
]
