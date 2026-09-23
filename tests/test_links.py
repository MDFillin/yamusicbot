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
