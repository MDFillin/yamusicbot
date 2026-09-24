import shutil
import subprocess

import pytest

from bot.audio import (
    ConversionError,
    TrackMeta,
    clean_year,
    normalize_cover,
    parse_caption,
    prepare_for_upload,
    read_mp3_tags,
    read_tags,
    safe_filename,
    tag_mp3,
)
from bot.handlers.upload import QueuedFile, prepare_file

FAKE_MP3 = b"\xff\xfb\x90\x64" + b"\x00" * 2000
JPEG = b"\xff\xd8\xff\xe0" + b"jpeg-cover" * 10
PNG = b"\x89PNG\r\n\x1a\n" + b"png-cover" * 10
needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="нужен ffmpeg")


def queued(file_id, name, size, caption, performer=None, title=None) -> QueuedFile:
    return QueuedFile(1, file_id, name, size, caption, performer, title)


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
    pf = queued("id", "rec_001.mp3", 100, "Кино - Кукушка")
    name, data, notes = await prepare_file(pf, FAKE_MP3)
    assert name == "Кино - Кукушка.mp3"
    assert read_mp3_tags(data) == ("Кино", "Кукушка")
    assert notes == ["теги: Кино — Кукушка"]


@pytest.mark.asyncio
async def test_prepare_file_uses_telegram_metadata_when_no_tags():
    pf = queued("id", "x.mp3", 100, None, performer="Artist", title="Title")
    name, data, _ = await prepare_file(pf, FAKE_MP3)
    assert name == "x.mp3"
    assert read_mp3_tags(data) == ("Artist", "Title")


@pytest.mark.asyncio
async def test_prepare_file_guesses_from_file_name_last():
    name, data, _ = await prepare_file(queued("id", "Кино - Пачка сигарет.mp3", 1, None), FAKE_MP3)
    assert read_mp3_tags(data) == ("Кино", "Пачка сигарет")
    tagged = tag_mp3(FAKE_MP3, artist="Из тегов", title="Песня")
    _, data, _ = await prepare_file(queued("id", "Кино - Пачка сигарет.mp3", 1, None), tagged)
    assert read_mp3_tags(data) == ("Из тегов", "Песня"), "теги файла важнее имени"


def test_read_tags_mp3_with_cover():
    tagged = tag_mp3(FAKE_MP3, artist="A", title="T", album="Alb", year=1999, cover=JPEG)
    meta = read_tags(tagged)
    assert (meta.title, meta.artist, meta.album, meta.year, meta.cover) == ("T", "A", "Alb", "1999", JPEG)
    assert read_tags(b"not audio") == TrackMeta()


@pytest.mark.asyncio
async def test_edit_overrides_only_changed_fields():
    tagged = tag_mp3(FAKE_MP3, artist="Старый", title="Трек", album="Альбом", year=2001, cover=JPEG)
    name, data, notes = await prepare_for_upload(
        "a.mp3", tagged, edit=TrackMeta(title="Новый трек", year="2024", cover=PNG))
    meta = read_tags(data)
    assert (meta.title, meta.artist, meta.album, meta.year) == ("Новый трек", "Старый", "Альбом", "2024")
    assert meta.cover == PNG
    assert name == "Старый - Новый трек.mp3"
    assert notes == ["теги: Старый — Новый трек", "год: 2024", "новая обложка"]

    _, data, notes = await prepare_for_upload("a.mp3", tagged, edit=TrackMeta(album="", remove_cover=True))
    meta = read_tags(data)
    assert meta.album is None and meta.cover is None and meta.title == "Трек"
    assert notes == ["альбом убран", "обложка убрана"]

    _, untouched, notes = await prepare_for_upload("a.mp3", tagged)
    assert untouched == tagged and notes == [], "без правок файл не трогаем"


def test_old_russian_tags_in_cp1251_are_readable():
    from mutagen.id3 import ID3, TIT2, TPE1

    tags = ID3()
    tags.add(TIT2(encoding=0, text="Кукушка".encode("cp1251").decode("latin-1")))
    tags.add(TPE1(encoding=0, text="Beyoncé"))
    import io

    buf = io.BytesIO(FAKE_MP3)
    tags.save(buf, v2_version=3)
    meta = read_tags(buf.getvalue())
    assert meta.title == "Кукушка" and meta.artist == "Beyoncé", "настоящий latin-1 не трогаем"


def test_clean_year():
    assert clean_year("1989-05-01") == "1989" and clean_year(" 2024 ") == "2024"
    assert clean_year("") == "" and clean_year(None) is None
    for bad in ("abc", "99", "3000"):
        with pytest.raises(ValueError):
            clean_year(bad)


@pytest.mark.asyncio
async def test_normalize_cover():
    assert await normalize_cover(JPEG) == JPEG and await normalize_cover(PNG) == PNG
    with pytest.raises(ConversionError, match="картинкой"):
        await normalize_cover(b"%PDF-1.4")


@needs_ffmpeg
@pytest.mark.asyncio
async def test_flac_keeps_its_cover_after_conversion(tmp_path):
    def ff(*args):
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True, cwd=tmp_path)

    ff("-f", "lavfi", "-i", "sine=duration=1", "-c:a", "flac", "plain.flac")
    ff("-f", "lavfi", "-i", "color=c=blue:s=64x64", "-frames:v", "1", "cover.jpg")
    ff("-i", "plain.flac", "-i", "cover.jpg", "-map", "0:a", "-map", "1", "-c", "copy",
       "-disposition:v", "attached_pic", "-metadata", "title=Флак", "-metadata", "artist=Автор", "song.flac")
    source = (tmp_path / "song.flac").read_bytes()
    assert read_tags(source).cover == (tmp_path / "cover.jpg").read_bytes()

    name, data, notes = await prepare_for_upload("song.flac", source, edit=TrackMeta(album="Новый альбом"))
    meta = read_tags(data)
    assert name == "song.mp3" and data[:3] == b"ID3"
    assert (meta.title, meta.artist, meta.album) == ("Флак", "Автор", "Новый альбом")
    assert meta.cover == (tmp_path / "cover.jpg").read_bytes()
    assert "обложка из файла сохранена" in notes


@pytest.mark.asyncio
async def test_prepare_file_non_mp3_without_ffmpeg(monkeypatch):
    monkeypatch.setattr("bot.audio.ffmpeg_available", lambda: False)
    pf = queued("id", "song.flac", 100, "A - B")
    name, data, notes = await prepare_file(pf, b"fLaC-data")
    assert name == "song.flac" and data == b"fLaC-data"
    assert "ffmpeg не установлен" in notes[0]
