import shutil
import subprocess

import pytest

from bot.audio import (
    ConversionError,
    TrackMeta,
    audio_format,
    clean_year,
    normalize_cover,
    parse_caption,
    prepare_for_upload,
    read_mp3_tags,
    read_tags,
    reencode_mp3,
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
    long = safe_filename("Rusted Citadel musical theme " * 10 + ".mp3")
    assert len(long) <= 150 and long.endswith(".mp3"), "длинное имя обрезается, расширение остаётся"


@pytest.mark.parametrize(("data", "fmt"), [
    (FAKE_MP3, "mp3"),
    (tag_mp3(FAKE_MP3, title="T", cover=JPEG), "mp3"),
    (b"\x00\x00\x00\x1cftypisom" + b"\x00" * 20, "mp4"),
    (b"\x1aE\xdf\xa3" + b"\x00" * 20, "webm"),
    (b"OggS" + b"\x00" * 20, "ogg"),
    (b"fLaC" + b"\x00" * 20, "flac"),
    (b"RIFF\x00\x00\x00\x00WAVEfmt ", "wav"),
    (b"\xff\xf1\x50\x80" + b"\x00" * 20, "aac"),
    (b"ID3\x03\x00\x00\x00\x00\x00\x00" + b"\x00\x00\x00\x1cftypM4A " + b"\x00" * 20, "mp4"),
    (b"<html>not audio</html>", None),
])
def test_audio_format_by_content(data, fmt):
    assert audio_format(data) == fmt


@pytest.mark.asyncio
async def test_mislabeled_file_without_ffmpeg_goes_as_is(monkeypatch):
    monkeypatch.setattr("bot.audio.ffmpeg_available", lambda: False)
    m4a = b"\x00\x00\x00\x1cftypisom" + b"\x00" * 200
    name, data, notes = await prepare_for_upload("song.mp3", m4a)
    assert data == m4a and "ffmpeg не установлен" in notes[0]


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


@needs_ffmpeg
@pytest.mark.asyncio
async def test_m4a_named_mp3_is_converted(tmp_path):
    """Скачанное с YouTube: называется .mp3, внутри M4A с тегами — Яндекс такое отвергает (415)."""
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=duration=1",
                    "-c:a", "aac", "-metadata", "title=Rusted Citadel", "-f", "mp4", "fake.mp3"],
                   check=True, cwd=tmp_path)
    source = (tmp_path / "fake.mp3").read_bytes()
    assert audio_format(source) == "mp4"
    name, data, notes = await prepare_for_upload("fake.mp3", source)
    assert name == "fake.mp3" and audio_format(data) == "mp3"
    assert read_tags(data).title == "Rusted Citadel"
    assert notes == ["внутри файла не MP3, а M4A/AAC — сконвертирован в MP3"]


# ---------- ffmpeg и чужие файлы ----------

def _ff(tmp_path, *args):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True, cwd=tmp_path)


@needs_ffmpeg
@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["track.m3u8", "track.flac"])
async def test_playlist_disguised_as_audio_cannot_read_server_files(tmp_path, name):
    """HLS-плейлист вместо аудио: раньше ffmpeg открывал по нему файлы сервера и бот загружал их в Яндекс."""
    _ff(tmp_path, "-f", "lavfi", "-i", "sine=duration=1", "secret.wav")
    playlist = f"#EXTM3U\n#EXT-X-TARGETDURATION:10\n#EXTINF:1.0,\n{tmp_path / 'secret.wav'}\n#EXT-X-ENDLIST\n"
    concat = f"ffconcat version 1.0\nfile '{tmp_path / 'secret.wav'}'\n"
    for payload in (playlist.encode(), concat.encode()):
        with pytest.raises(ConversionError, match="распознать"):
            await prepare_for_upload(name, payload)
        # «.mp3» с непонятным содержимым уходит в Яндекс как есть, а перекодировка после 415 читает его только как MP3
        assert (await prepare_for_upload("track.mp3", payload))[1] == payload
        with pytest.raises(ConversionError):
            await reencode_mp3(payload)


@needs_ffmpeg
@pytest.mark.asyncio
async def test_unrecognized_audio_is_still_converted(tmp_path):
    """Формат, который бот сам не узнаёт (AC3), ffmpeg угадывает — но только среди аудиоформатов."""
    _ff(tmp_path, "-f", "lavfi", "-i", "sine=duration=1", "-c:a", "ac3", "-f", "ac3", "song.ac3")
    source = (tmp_path / "song.ac3").read_bytes()
    assert audio_format(source) is None
    name, data, _ = await prepare_for_upload("song.ac3", source)
    assert name == "song.mp3" and audio_format(data) == "mp3"


@needs_ffmpeg
@pytest.mark.asyncio
async def test_silence_bomb_does_not_fill_the_disk(tmp_path, monkeypatch):
    """Маленький FLAC с часами тишины превратился бы в гигабайты MP3."""
    monkeypatch.setattr("bot.audio.MAX_CONVERTED_BYTES", 200_000)
    _ff(tmp_path, "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "60", "-c:a", "flac", "silence.flac")
    with pytest.raises(ConversionError, match="больше"):
        await prepare_for_upload("silence.flac", (tmp_path / "silence.flac").read_bytes())


@needs_ffmpeg
@pytest.mark.asyncio
async def test_slow_conversion_is_killed(tmp_path, monkeypatch):
    monkeypatch.setattr("bot.audio.FFMPEG_TIMEOUT", 0.05)
    _ff(tmp_path, "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "600", "-c:a", "flac", "long.flac")
    with pytest.raises(ConversionError, match="слишком долго"):
        await prepare_for_upload("long.flac", (tmp_path / "long.flac").read_bytes())


@needs_ffmpeg
@pytest.mark.asyncio
async def test_huge_cover_image_is_rejected(tmp_path):
    """PNG 8000×8000 весит мало, а в памяти занял бы сотни мегабайт."""
    _ff(tmp_path, "-f", "lavfi", "-i", "color=c=black:s=8000x8000", "-frames:v", "1", "-f", "image2", "bomb.png")
    bomb = (tmp_path / "bomb.png").read_bytes() + b"\0" * 1_600_000  # крупнее 1,5 МБ — пойдёт через ffmpeg
    with pytest.raises(ConversionError, match="мегапикселей"):
        await normalize_cover(bomb)
    _ff(tmp_path, "-f", "lavfi", "-i", "color=c=red:s=2000x1500", "-frames:v", "1", "-f", "image2", "big.png")
    big = (tmp_path / "big.png").read_bytes() + b"\0" * 1_600_000
    assert (await normalize_cover(big))[:2] == b"\xff\xd8", "обычная большая картинка пережимается в JPEG"
