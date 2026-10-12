"""Проверка Ynison (точный подсчёт) с сервера: работает ли он сейчас у Яндекса и не придержан ли именно бот.

Запуск на сервере:  docker compose exec bot python -m bot.diag_ynison

Подключается к Ynison аккаунтом владельца бота (ADMIN_IDS) дважды: с устройством, которым пользуется бот, и с
новым — и печатает, что ответил Яндекс. Токен на экран не выводится.
"""

from __future__ import annotations

import asyncio
import secrets

from bot import net, ynison
from bot.config import ConfigError, load_config
from bot.crypto import TokenCipher
from bot.storage import Storage


class _Got(Exception):
    def __init__(self, snap: ynison.Snapshot | None) -> None:
        self.snap = snap


async def probe(token: str, device_id: str) -> tuple[str, str]:
    """(итог: ok | wait | auth | fail, что ответил Яндекс)."""
    async def on_frame(frame: dict, raw: str) -> None:
        raise _Got(ynison.parse_state(frame))

    try:
        async with net.yandex_session(timeout=net.timeout(None)) as http:
            await asyncio.wait_for(ynison.listen(http, token, device_id, on_frame), 40)
    except _Got as got:
        snap = got.snap
        playing = f"сейчас в плеере трек {snap.track_id}" if snap and snap.track_id else "плеер пуст"
        return "ok", f"✅ Ynison отвечает: {playing}"
    except ynison.YnisonGoAway as e:
        return "wait", f"⏸ Яндекс: «{e}», просит не подключаться {e.seconds // 60} мин"
    except ynison.YnisonAuthError as e:
        return "auth", f"🔑 Яндекс не принял вход: {e}"
    except TimeoutError:
        return "fail", "⌛ за 40 секунд Яндекс ничего не прислал"
    except Exception as e:
        return "fail", f"❌ {type(e).__name__}: {str(e)[:200]}"
    return "fail", "❌ Яндекс закрыл соединение, ничего не прислав"


async def main() -> None:
    try:
        config = load_config()
    except ConfigError as e:
        raise SystemExit(f"Ошибка настройки: {e}") from e
    net.configure(config.yandex_proxy)
    store = Storage(config.data_dir / "bot.db", TokenCipher.load(config.data_dir, config.encryption_key))
    try:
        owner = next((uid for uid in sorted(config.admin_ids) if store.get_account(uid)), None)
        if owner is None:
            raise SystemExit("Нужен владелец бота (ADMIN_IDS) с подключённой Яндекс Музыкой")
        token = store.get_account(owner)[0]
        bot_device = store.get_setting(owner, "ynison_device") or secrets.token_hex(8)
    finally:
        store.close()

    print(f"Ynison, аккаунт владельца {owner}" + (f", через прокси {net.mask(config.yandex_proxy)}"
                                                  if config.yandex_proxy else "") + "\n")
    bot_kind, bot_text = await probe(token, bot_device)
    print(f"Устройство бота:  {bot_text}")
    new_kind, new_text = await probe(token, secrets.token_hex(8))
    print(f"Новое устройство: {new_text}\n")

    if bot_kind == "ok":
        print("Итог: Ynison работает. Точный подсчёт подключится сам "
              "(или перезапустите бота: docker compose restart bot).")
    elif bot_kind == "auth":
        print("Итог: вход в Яндекс устарел — подключите аккаунт заново (/login).")
    elif new_kind == "ok":
        print("Итог: Ynison работает, но устройство бота Яндекс придержал — бот сам подключится, когда срок выйдет.")
    elif bot_kind == new_kind == "wait":
        print("Итог: Ynison у Яндекса сейчас недоступен — дело не в боте. Проверьте с телефона: включите трек в "
              "приложении и откройте music.yandex.ru — если сайт не видит, что играет на телефоне, у Яндекса сбой, "
              "надо ждать. Если видит — Яндекс не пускает именно сервер бота (попробуйте YANDEX_PROXY).")
    else:
        print("Итог: до Ynison не достучаться. Проверьте связь: docker compose exec bot python -m bot.diag_net")


if __name__ == "__main__":
    asyncio.run(main())
