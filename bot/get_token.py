"""Получение токена Яндекс Музыки: python -m bot.get_token

Показывает ссылку и код; после подтверждения входа на странице Яндекса выводит токен
и, если рядом есть файл .env, записывает его туда в YANDEX_MUSIC_TOKEN.
"""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

from yandex_music import ClientAsync
from yandex_music.exceptions import YandexMusicError


def save_to_env(token: str, env_path: Path = Path(".env")) -> bool:
    if not env_path.exists():
        return False
    text = env_path.read_text("utf-8")
    line = f"YANDEX_MUSIC_TOKEN={token}"
    if re.search(r"^YANDEX_MUSIC_TOKEN=.*$", text, flags=re.M):
        text = re.sub(r"^YANDEX_MUSIC_TOKEN=.*$", lambda _: line, text, flags=re.M)
    else:
        text = text.rstrip("\n") + f"\n{line}\n"
    env_path.write_text(text, "utf-8")
    return True


async def main() -> None:
    def on_code(code) -> None:
        print(f"\n1. Откройте {code.verification_url}")
        print(f"2. Введите код: {code.user_code}")
        print("3. Подтвердите вход своим аккаунтом Яндекса. Жду…\n")

    client = ClientAsync()
    try:
        token = await client.device_auth(on_code=on_code, device_name="yamusicbot")
    except YandexMusicError as e:
        sys.exit(f"Не удалось получить токен: {e}")

    await client.init()
    print(f"Готово! Аккаунт: {client.me.account.login}\n")
    if save_to_env(token.access_token):
        print("Токен записан в .env (YANDEX_MUSIC_TOKEN).")
    else:
        print(f"YANDEX_MUSIC_TOKEN={token.access_token}")
        print("\nВставьте эту строку в .env.")


if __name__ == "__main__":
    asyncio.run(main())
