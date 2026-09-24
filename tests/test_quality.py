"""Выбор MP3 нужного качества у Яндекса."""

from types import SimpleNamespace as NS

from bot.ym import YandexMusic


def info(kbps, preview=False, codec="mp3"):
    async def get_direct_link_async():
        return f"https://cdn/{kbps}.mp3"

    return NS(codec=codec, preview=preview, bitrate_in_kbps=kbps, direct_link=f"https://cdn/{kbps}.mp3",
              get_direct_link_async=get_direct_link_async)


def track(*infos):
    async def get_download_info_async():
        return list(infos)

    return NS(available=True, get_download_info_async=get_download_info_async)


async def test_best_mp3_not_above_limit():
    ym = YandexMusic("t", max_bitrate=320)
    t = track(info(320), info(192), info(128), info(64), info(256, codec="aac"))
    assert await ym.direct_link(t) == "https://cdn/320.mp3"
    assert await ym.direct_link(t, 192) == "https://cdn/192.mp3"
    assert await ym.direct_link(t, 100) == "https://cdn/64.mp3"
    assert await ym.direct_link(track(info(128)), 64) == "https://cdn/128.mp3", "нет ниже — берём самое лёгкое"


async def test_server_limit_wins_over_user_choice():
    ym = YandexMusic("t", max_bitrate=192)
    assert await ym.direct_link(track(info(320), info(192)), 320) == "https://cdn/192.mp3"
