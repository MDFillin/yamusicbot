"""Проверка связи с Яндексом тем же путём, что и бот (напрямую или через YANDEX_PROXY).

Запуск на сервере:  docker compose exec bot python -m bot.diag_net
"""

from __future__ import annotations

import asyncio
import time

from bot import net
from bot.config import ConfigError, load_config

TARGETS = (
    ("API Яндекс Музыки", "https://api.music.yandex.net/account/status"),
    ("Ynison (плеер)", "https://ynison.music.yandex.ru/"),
    ("ya.ru", "https://ya.ru/"),
)
ROUNDS = 3


async def probe(url: str) -> tuple[str, float, bool]:
    started = time.monotonic()
    try:
        # Каждый раз новая сессия — новое соединение: так видно, насколько надёжен сам маршрут.
        async with net.yandex_session(timeout=net.timeout(40)) as session, session.get(url) as resp:
            return f"ответ {resp.status}", time.monotonic() - started, True
    except Exception as e:
        return f"нет связи: {type(e).__name__} {str(e)[:80]}", time.monotonic() - started, False


async def main() -> None:
    try:
        config = load_config()
    except ConfigError as e:
        raise SystemExit(f"Ошибка настройки: {e}") from e
    net.configure(config.yandex_proxy)
    print(f"Яндекс: {'через прокси ' + net.mask(config.yandex_proxy) if config.yandex_proxy else 'напрямую'}\n")
    ok = total = 0
    slow = 0
    for name, url in TARGETS:
        print(name)
        for i in range(ROUNDS):
            result, seconds, good = await probe(url)
            total, ok, slow = total + 1, ok + good, slow + (good and seconds > 5)
            print(f"  {i + 1}. {result} за {seconds:.1f} с")
    print()
    if ok == total and not slow:
        print("✅ Связь с Яндексом в порядке.")
    elif ok == total:
        print("⚠️ Связь есть, но медленная: соединения устанавливаются не с первой попытки.")
    elif ok:
        print(f"⚠️ Связь нестабильна: прошло {ok} из {total}. Маршрут до Яндекса частично сломан — "
              "помогут хостер или прокси (README, «Прокси для Яндекса»).")
    else:
        print("❌ Яндекс недоступен. " + ("Проверьте прокси: работает ли второй сервер и туннель "
                                          "(docker compose logs yandex-tunnel)." if config.yandex_proxy else
                                          "Нужен хостер или прокси (README, «Прокси для Яндекса»)."))


if __name__ == "__main__":
    asyncio.run(main())
