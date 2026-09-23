"""Работа с аудиофайлами: ID3-теги, конвертация в MP3, имена файлов."""

from __future__ import annotations

import asyncio
import io
import re
import shutil
import tempfile
from pathlib import Path

from mutagen.id3 import APIC, ID3, TALB, TDRC, TIT2, TPE1, ID3NoHeaderError

# Эти форматы бот понимает как аудио, если их прислали документом.
AUDIO_EXTENSIONS = {".mp3", ".flac", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".wav", ".wma", ".alac", ".aiff", ".ape"}

_CAPTION_SPLIT = re.compile(r"\s+[-–—]\s+")
_BAD_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def safe_filename(name: str, default: str = "track") -> str:
    name = _BAD_CHARS.sub("_", name).strip(" .")
    return name[:150] or default


def parse_caption(caption: str | None) -> tuple[str, str] | None:
    """«Исполнитель - Название» -> (исполнитель, название)."""
    if not caption:
        return None
    parts = _CAPTION_SPLIT.split(caption.strip(), maxsplit=1)
    if len(parts) != 2 or not all(p.strip() for p in parts):
        return None
    return parts[0].strip(), parts[1].strip()


def is_audio_filename(filename: str | None) -> bool:
    return bool(filename) and Path(filename).suffix.lower() in AUDIO_EXTENSIONS


def tag_mp3(
    data: bytes,
    *,
    title: str | None = None,
    artist: str | None = None,
    album: str | None = None,
    year: int | None = None,
    cover: bytes | None = None,
) -> bytes:
    """Прописывает ID3-теги в MP3. Возвращает новые байты файла."""
    buf = io.BytesIO(data)
    try:
        tags = ID3(buf)
    except ID3NoHeaderError:
        tags = ID3()
    if title:
        tags.setall("TIT2", [TIT2(encoding=3, text=title)])
    if artist:
        tags.setall("TPE1", [TPE1(encoding=3, text=artist)])
    if album:
        tags.setall("TALB", [TALB(encoding=3, text=album)])
    if year:
        tags.setall("TDRC", [TDRC(encoding=3, text=str(year))])
    if cover:
        tags.setall("APIC", [APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=cover)])
    buf.seek(0)
    tags.save(buf, v2_version=3)
    return buf.getvalue()


def read_mp3_tags(data: bytes) -> tuple[str | None, str | None]:
    """(исполнитель, название) из ID3, если они есть."""
    try:
        tags = ID3(io.BytesIO(data))
    except Exception:
        return None, None
    artist = str(tags["TPE1"].text[0]) if "TPE1" in tags and tags["TPE1"].text else None
    title = str(tags["TIT2"].text[0]) if "TIT2" in tags and tags["TIT2"].text else None
    return artist, title


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


class ConversionError(RuntimeError):
    pass


async def convert_to_mp3(data: bytes, source_ext: str, bitrate: int = 320) -> bytes:
    """Конвертирует аудио в MP3 через ffmpeg (теги переносятся)."""
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / f"in{source_ext or '.bin'}"
        dst = Path(tmp) / "out.mp3"
        src.write_bytes(data)
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(src),
            "-map", "0:a:0", "-map_metadata", "0",
            "-codec:a", "libmp3lame", "-b:a", f"{bitrate}k", "-id3v2_version", "3",
            str(dst),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0 or not dst.exists():
            raise ConversionError(stderr.decode(errors="replace").strip()[-500:] or "ffmpeg завершился с ошибкой")
        return dst.read_bytes()


async def prepare_for_upload(
    file_name: str,
    data: bytes,
    *,
    artist: str | None = None,
    title: str | None = None,
    fallback_artist: str | None = None,
    fallback_title: str | None = None,
) -> tuple[str, bytes, list[str]]:
    """Готовит файл к загрузке в Яндекс Музыку. Возвращает (имя файла, байты, заметки для пользователя).

    - не-MP3 конвертируется в MP3, если есть ffmpeg;
    - artist/title (указаны пользователем) записываются в теги и в имя файла;
    - fallback_* (например, из метаданных Telegram) пишутся, только если своих тегов в файле нет.
    """
    notes: list[str] = []
    name = safe_filename(file_name)
    ext = Path(name).suffix.lower()

    if ext != ".mp3":
        if ffmpeg_available():
            data = await convert_to_mp3(data, ext)
            name = f"{Path(name).stem}.mp3"
            notes.append(f"сконвертирован из {ext or 'неизвестного формата'} в MP3")
        else:
            notes.append(f"{ext or 'файл'} загружен как есть (ffmpeg не установлен, Яндекс надёжнее принимает MP3)")

    if name.lower().endswith(".mp3"):
        if artist and title:
            data = tag_mp3(data, artist=artist, title=title)
            name = safe_filename(f"{artist} - {title}") + ".mp3"
            notes.append(f"теги: {artist} — {title}")
        else:
            tag_artist, tag_title = read_mp3_tags(data)
            new_artist = tag_artist or artist or fallback_artist
            new_title = tag_title or title or fallback_title
            if (new_artist, new_title) != (tag_artist, tag_title):
                data = tag_mp3(data, artist=new_artist, title=new_title)
    return name, data, notes
