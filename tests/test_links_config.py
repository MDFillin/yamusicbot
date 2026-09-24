import pytest

from bot.links import parse_link


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("https://music.yandex.ru/album/123/track/456", ("track", {"album": "123", "track": "456"})),
        ("вот https://music.yandex.com/album/1/track/2?utm_source=x", ("track", {"album": "1", "track": "2"})),
        ("https://music.yandex.ru/track/789", ("track", {"track": "789"})),
        ("music.yandex.by/album/42", ("album", {"album": "42"})),
        (
            "https://music.yandex.ru/users/some.login/playlists/1003",
            ("playlist", {"owner": "some.login", "playlist_kind": "1003"}),
        ),
        (
            "https://music.yandex.ru/playlists/lk.0c2f4a1e-1b2c-4d5e-8f90-123456789abc",
            ("playlist_uuid", {"uuid": "lk.0c2f4a1e-1b2c-4d5e-8f90-123456789abc"}),
        ),
        ("https://music.yandex.kz/artist/9876/tracks", ("artist", {"artist": "9876"})),
    ],
)
def test_parse_link(text, expected):
    kind, fields = expected
    link = parse_link(text)
    assert link is not None
    assert link.kind == kind
    for key, value in fields.items():
        assert getattr(link, key) == value


@pytest.mark.parametrize("text", ["просто текст", "https://example.com/album/1", "https://music.yandex.ru/"])
def test_parse_link_none(text):
    assert parse_link(text) is None


def test_webapp_url_must_be_https(monkeypatch):
    from bot.config import ConfigError, load_config

    monkeypatch.setenv("BOT_TOKEN", "1:x")
    monkeypatch.setenv("YANDEX_MUSIC_TOKEN", "y0")
    monkeypatch.setenv("WEBAPP_URL", "http://insecure.example.com")
    with pytest.raises(ConfigError, match="https"):
        load_config()
    monkeypatch.setenv("WEBAPP_URL", "https://music.example.com/")
    assert load_config().webapp_url == "https://music.example.com"


def test_telegram_proxy(monkeypatch):
    from bot.config import ConfigError, load_config
    from bot.main import build_bot

    monkeypatch.setenv("BOT_TOKEN", "1:x")
    monkeypatch.setenv("YANDEX_MUSIC_TOKEN", "y0")
    monkeypatch.setenv("TELEGRAM_PROXY", "1.2.3.4:1080")
    with pytest.raises(ConfigError, match="socks5"):
        load_config()
    monkeypatch.setenv("TELEGRAM_PROXY", "socks5://user:pass@1.2.3.4:1080")
    config = load_config()
    bot = build_bot(config)
    assert bot.session.proxy == "socks5://user:pass@1.2.3.4:1080"
    monkeypatch.delenv("TELEGRAM_PROXY")
    assert build_bot(load_config()).session.proxy is None


def test_fxtunnel_domain_sets_webapp_url(monkeypatch):
    from bot.config import ConfigError, load_config

    monkeypatch.setenv("BOT_TOKEN", "1:x")
    monkeypatch.setenv("YANDEX_MUSIC_TOKEN", "y0")
    monkeypatch.delenv("WEBAPP_URL", raising=False)
    monkeypatch.setenv("FXTUNNEL_DOMAIN", "BobikMusic228")
    assert load_config().webapp_url == "https://bobikmusic228.fxtun.dev"

    monkeypatch.setenv("WEBAPP_URL", "https://other.example.com")
    assert load_config().webapp_url == "https://other.example.com", "явный WEBAPP_URL важнее"

    for bad in ("ab", "-music", "my_music", "x" * 33):
        monkeypatch.setenv("FXTUNNEL_DOMAIN", bad)
        with pytest.raises(ConfigError, match="FXTUNNEL_DOMAIN"):
            load_config()
