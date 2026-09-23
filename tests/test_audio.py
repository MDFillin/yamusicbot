import pytest

from bot.audio import parse_caption, read_mp3_tags, safe_filename, tag_mp3
from bot.handlers.upload import PendingFile, prepare_file

FAKE_MP3 = b"\xff\xfb\x90\x64" + b"\x00" * 2000


def test_parse_caption():
    assert parse_caption("Кино - Группа крови") == ("Кино", "Группа крови")
    assert parse_caption("AC/DC — Back In Black") == ("AC/DC", "Back In Black")
    assert parse_caption("a-ha - Take On Me") == ("a-ha", "Take On Me")
    assert parse_caption("просто подпись") is None
    assert parse_caption(None) is None
    assert parse_caption(" - ") is None


def test_safe_filename():
    assert safe_filename('AC/DC: "Hells Bells"?.mp3') == "AC_DC_ _Hells Bells__.mp3"
    assert safe_filename("...") == "track"


def test_tag_roundtrip():
    tagged = tag_mp3(FAKE_MP3, artist="Исполнитель", title="Песня", album="Альбом", year=2024, cover=b"\xff\xd8jpeg")
    assert tagged.endswith(FAKE_MP3)
    assert read_mp3_tags(tagged) == ("Исполнитель", "Песня")
    retagged = tag_mp3(tagged, title="Другая")
    assert read_mp3_tags(retagged) == ("Исполнитель", "Другая")
    assert read_mp3_tags(FAKE_MP3) == (None, None)


@pytest.mark.asyncio
async def test_prepare_file_caption_sets_tags_and_name():
    pf = PendingFile("id", "rec_001.mp3", 100, "Кино - Кукушка")
    name, data, notes = await prepare_file(pf, FAKE_MP3)
    assert name == "Кино - Кукушка.mp3"
    assert read_mp3_tags(data) == ("Кино", "Кукушка")
    assert notes == ["теги: Кино — Кукушка"]


@pytest.mark.asyncio
async def test_prepare_file_uses_telegram_metadata_when_no_tags():
    pf = PendingFile("id", "x.mp3", 100, None, performer="Artist", title="Title")
    name, data, _ = await prepare_file(pf, FAKE_MP3)
    assert name == "x.mp3"
    assert read_mp3_tags(data) == ("Artist", "Title")


@pytest.mark.asyncio
async def test_prepare_file_non_mp3_without_ffmpeg(monkeypatch):
    monkeypatch.setattr("bot.audio.ffmpeg_available", lambda: False)
    pf = PendingFile("id", "song.flac", 100, "A - B")
    name, data, notes = await prepare_file(pf, b"fLaC-data")
    assert name == "song.flac" and data == b"fLaC-data"
    assert "ffmpeg не установлен" in notes[0]
