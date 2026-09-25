"""Работа с аудиофайлами: ID3-теги, конвертация в MP3, имена файлов."""

from __future__ import annotations

import asyncio
import base64
import io
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import mutagen
from mutagen.flac import Picture
from mutagen.id3 import APIC, ID3, TALB, TDRC, TIT2, TPE1, ID3NoHeaderError

# Эти форматы бот понимает как аудио, если их прислали документом.
AUDIO_EXTENSIONS = {".mp3", ".flac", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".wav", ".wma", ".alac", ".aiff", ".ape"}

_CAPTION_SPLIT = re.compile(r"\s+[-–—]\s+")
_BAD_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_YEAR = re.compile(r"\d{4}")

MAX_COVER_BYTES = 10 * 1024 * 1024
COVER_MAX_SIDE = 1200  # px: больше обложке не нужно, а файл трека от неё раздувается


@dataclass
class TrackMeta:
    """Данные трека: название, исполнитель, альбом, год, обложка.

    В правке пользователя None значит «оставить как в файле», а пустая строка — «очистить».
    """

    title: str | None = None
    artist: str | None = None
    album: str | None = None
    year: str | None = None
    cover: bytes | None = field(default=None, repr=False)
    remove_cover: bool = False

    @property
    def changed(self) -> bool:
        return any(v is not None for v in (self.title, self.artist, self.album, self.year, self.cover)) \
            or self.remove_cover


def clean_year(value: str | None) -> str | None:
    """Год из тега или ввода: «1989», «1989-05-01» -> «1989»; пустая строка остаётся пустой (очистить)."""
    if value is None or not value.strip():
        return value.strip() if value is not None else None
    m = _YEAR.search(value)
    if not m or not 1000 <= int(m.group()) <= 2100:
        raise ValueError("Год — четыре цифры, например 2024")
    return m.group()


def safe_filename(name: str, default: str = "track", limit: int = 150) -> str:
    """Имя файла без опасных символов; длинное обрезается, но расширение сохраняется."""
    name = _BAD_CHARS.sub("_", name).strip(" .")
    if len(name) > limit:
        stem, dot, ext = name.rpartition(".")
        if dot and stem and 1 <= len(ext) <= 5:
            name = stem[:limit - len(ext) - 1].rstrip(" .") + "." + ext
        else:
            name = name[:limit].rstrip(" .")
    return name or default


# Правила ниже (разбор «Исполнитель - Название», имя файла, cp1251, объединение правок с тегами) повторены
# в bot/web/static/meta.js — там они нужны для мгновенного предпросмотра, пока файл ещё не загружен.
# Обе реализации проверяются на одних и тех же примерах: tests/fixtures/meta_cases.json.

def parse_caption(caption: str | None) -> tuple[str, str] | None:
    """«Исполнитель - Название» -> (исполнитель, название). Делится по первому тире в окружении пробелов."""
    if not caption:
        return None
    parts = _CAPTION_SPLIT.split(caption.strip(), maxsplit=1)
    if len(parts) != 2 or not all(p.strip() for p in parts):
        return None
    return parts[0].strip(), parts[1].strip()


def guess_from_name(file_name: str) -> tuple[str | None, str | None]:
    """(исполнитель, название) по имени файла, когда в нём нет тегов: «Кино_-_Кукушка.mp3» -> (Кино, Кукушка)."""
    stem = re.sub(r"\.[^.]+$", "", file_name).replace("_", " ").strip()
    if (parsed := parse_caption(stem)) is not None:
        return parsed
    return None, stem or None


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


# Что на самом деле внутри аудиофайла (расширение бывает обманчивым: скачанное с YouTube часто
# называется .mp3, а внутри M4A или WebM — такое Яндекс отвергает с UNSUPPORTED_MEDIA_TYPE).
FORMAT_NAMES = {"mp4": "M4A/AAC", "webm": "WebM", "ogg": "OGG", "flac": "FLAC", "wav": "WAV", "aiff": "AIFF",
                "wma": "WMA", "ape": "APE", "aac": "AAC"}


def _mpeg_frame(b0: int, b1: int, b2: int) -> str | None:
    """Заголовок кадра MPEG-аудио: "mp3" (слои II/III), "aac" (ADTS) или None."""
    if b0 != 0xFF or (b1 & 0xE0) != 0xE0:
        return None
    layer = (b1 >> 1) & 3
    if layer == 0:
        return "aac" if (b1 & 0xF0) == 0xF0 else None
    if (b1 >> 3) & 3 == 1 or layer == 3:  # зарезервированная версия; слой I как MP3 не годится
        return None
    if (b2 >> 4) in (0, 0xF) or (b2 >> 2) & 3 == 3:  # неверный битрейт или частота
        return None
    return "mp3"


def audio_format(data: bytes | memoryview, _depth: int = 0) -> str | None:
    """Формат аудио по содержимому: mp3, mp4, webm, ogg, flac, wav, aiff, wma, ape, aac или None."""
    head = bytes(data[:12])
    if head[:4] == b"fLaC":
        return "flac"
    if head[4:8] == b"ftyp":
        return "mp4"
    if head[:4] == b"OggS":
        return "ogg"
    if head[:4] == b"\x1aE\xdf\xa3":
        return "webm"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav"
    if head[:4] == b"FORM" and head[8:12] in (b"AIFF", b"AIFC"):
        return "aiff"
    if head[:4] == b"0&\xb2u":
        return "wma"
    if head[:4] == b"MAC ":
        return "ape"
    start = 0
    if head[:3] == b"ID3" and len(data) >= 10:
        size = (data[6] & 0x7F) << 21 | (data[7] & 0x7F) << 14 | (data[8] & 0x7F) << 7 | (data[9] & 0x7F)
        start = 10 + size + (10 if data[5] & 0x10 else 0)
        if _depth == 0 and (inner := audio_format(memoryview(data)[start:], 1)) not in (None, "mp3"):
            return inner  # ID3 перед чужим контейнером (так делают некоторые программы и прежняя версия бота)
        tag_end = start
        while start < len(data) and start < tag_end + 65536 and data[start] == 0:
            start += 1  # нули-«набивка» после тега
    for i in range(start, min(len(data), start + 4096) - 2):
        if data[i] == 0xFF and (kind := _mpeg_frame(data[i], data[i + 1], data[i + 2])):
            return kind
    return None


def image_type(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8"):
        return "jpeg"
    if data.startswith(b"\x89PNG"):
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[:4] in (b"GIF8",):
        return "gif"
    return None


def _first(value) -> str | None:
    if isinstance(value, list):
        value = value[0] if value else None
    text = str(value).strip() if value is not None else ""
    return text or None


_CP1251_LETTERS = set(range(0xC0, 0x100)) | {0xA8, 0xB8}  # кириллица и Ёё в cp1251
_CP1251_PUNCT = {0x85, 0x91, 0x92, 0x93, 0x94, 0x96, 0x97, 0xAB, 0xB9, 0xBB}  # … ‘ ’ “ ” – — « № »
_MIXED_WORD = re.compile(r"[A-Za-z][А-яЁё]|[А-яЁё][A-Za-z]")


def decode_latin1(raw: bytes) -> str:
    """Текст, записанный как latin-1. Старые русские MP3 пишут так cp1251: если все высокие байты — кириллица
    (и типографские знаки cp1251: тире, кавычки «», многоточие, №), читаем как cp1251 — кроме случаев
    вроде «Beyoncé» (дал бы «Beyoncщ»), это настоящий latin-1."""
    high = [b for b in raw if b >= 0x80]
    if any(b in _CP1251_LETTERS for b in high) and all(b in _CP1251_LETTERS or b in _CP1251_PUNCT for b in high):
        fixed = raw.decode("cp1251")
        if not _MIXED_WORD.search(fixed):
            return fixed
    return raw.decode("latin-1")


def _id3_text(frame) -> str | None:
    """Текст ID3-кадра (кодировка 0 — latin-1, под которым часто прячется cp1251)."""
    text = _first(frame.text)
    if text and frame.encoding == 0:
        try:
            return decode_latin1(text.encode("latin-1"))
        except UnicodeEncodeError:
            return text
    return text


def _year_or_none(value) -> str | None:
    try:
        return clean_year(_first(value)) or None
    except ValueError:
        return None


def read_tags(data: bytes) -> TrackMeta:
    """Теги и обложка из файла: MP3 (ID3), FLAC, OGG/Opus, M4A. Чего нет или не удалось прочитать — None."""
    try:
        if data[:3] == b"ID3":
            tags, audio = ID3(io.BytesIO(data)), None
        else:
            audio = mutagen.File(io.BytesIO(data))
            tags = audio.tags if audio is not None else None
    except Exception:
        return TrackMeta()
    if tags is None:
        return TrackMeta()

    if isinstance(tags, ID3):
        apic = tags.getall("APIC")
        cover = next((a.data for a in apic if a.type == 3), apic[0].data if apic else None)
        return TrackMeta(
            title=_id3_text(tags["TIT2"]) if "TIT2" in tags else None,
            artist=_id3_text(tags["TPE1"]) if "TPE1" in tags else None,
            album=_id3_text(tags["TALB"]) if "TALB" in tags else None,
            year=_year_or_none(tags["TDRC"].text) if "TDRC" in tags else None,
            cover=cover,
        )

    def get(*keys: str):
        for key in keys:
            try:
                if key in tags:
                    return tags[key]
            except (KeyError, ValueError, TypeError):
                continue
        return None

    cover = None
    pictures = getattr(audio, "pictures", None)  # FLAC
    if pictures:
        cover = next((p.data for p in pictures if p.type == 3), pictures[0].data)
    elif (block := _first(get("metadata_block_picture"))) is not None:  # OGG/Opus
        try:
            cover = Picture(base64.b64decode(block)).data
        except Exception:
            cover = None
    elif (covr := get("covr")) is not None:  # M4A
        cover = bytes(covr[0]) if covr else None
    return TrackMeta(
        title=_first(get("title", "\xa9nam")),
        artist=_first(get("artist", "\xa9ART")),
        album=_first(get("album", "\xa9alb")),
        year=_year_or_none(get("date", "year", "\xa9day")),
        cover=cover,
    )


def write_mp3_tags(data: bytes, meta: TrackMeta) -> bytes:
    """Записывает в MP3 ровно эти данные: пустые поля и обложка удаляются."""
    buf = io.BytesIO(data)
    try:
        tags = ID3(buf)
    except ID3NoHeaderError:
        tags = ID3()
    for frame, cls, value in (("TIT2", TIT2, meta.title), ("TPE1", TPE1, meta.artist),
                              ("TALB", TALB, meta.album), ("TDRC", TDRC, meta.year)):
        if value:
            tags.setall(frame, [cls(encoding=3, text=value)])
        else:
            tags.delall(frame)
    if meta.cover:
        mime = "image/png" if image_type(meta.cover) == "png" else "image/jpeg"
        tags.setall("APIC", [APIC(encoding=3, mime=mime, type=3, desc="Cover", data=meta.cover)])
    else:
        tags.delall("APIC")
    buf.seek(0)
    tags.save(buf, v2_version=3)
    return buf.getvalue()


# ffmpeg разбирает файлы от кого угодно, поэтому формат ему называем сами (по содержимому, а не по имени):
# иначе файл «x.m3u8» с HLS-плейлистом внутри заставил бы его читать файлы сервера или ходить по ссылкам.
_AUDIO_DEMUXERS = {"mp3": "mp3", "mp4": "mov", "webm": "matroska", "ogg": "ogg", "flac": "flac", "wav": "wav",
                   "aiff": "aiff", "wma": "asf", "ape": "ape", "aac": "aac"}
# Что ffmpeg может угадать сам, если формат по содержимому не опознали: без плейлистов (hls, concat) и прочего,
# что открывает другие файлы или адреса.
_PROBE_FORMATS = ",".join(sorted({*_AUDIO_DEMUXERS.values(), "ac3", "eac3", "dts", "amr", "wv", "tta", "caf", "w64",
                                  "mpc", "mpc8"}))
_IMAGE_DEMUXERS = {"jpeg": "jpeg_pipe", "png": "png_pipe", "webp": "webp_pipe", "gif": "gif"}
FFMPEG_TIMEOUT = 600  # сек: трёхчасовой FLAC конвертируется за пару минут
COVER_TIMEOUT = 60
MAX_CONVERTED_BYTES = 400 * 1024 * 1024  # ~2 ч 50 мин в MP3 320 kbps; защищает диск от «бомб» из тишины
MAX_COVER_PIXELS = 40_000_000  # картинка 50000×50000 из 10 МБ PNG заняла бы гигабайты памяти
_ffmpeg_slots = asyncio.Semaphore(2)  # одновременно работающих ffmpeg на весь сервер


class ConversionError(RuntimeError):
    pass


class FFmpegFailed(ConversionError):
    """ffmpeg не смог разобрать или сконвертировать файл."""


async def _ffmpeg(src: bytes, out_args: list[str], dst_name: str, *, demuxer: str | None = None,
                  in_args: tuple[str, ...] = (), timeout: float | None = None,
                  max_output: int | None = None) -> bytes:
    """Запускает ffmpeg над байтами src: только локальный файл, заданный формат, ограничены время и размер."""
    timeout = timeout or FFMPEG_TIMEOUT
    max_output = max_output or MAX_CONVERTED_BYTES
    async with _ffmpeg_slots:
        with tempfile.TemporaryDirectory() as tmp:
            src_path = Path(tmp) / "input"
            dst_path = Path(tmp) / dst_name
            src_path.write_bytes(src)
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                "-protocol_whitelist", "file", *in_args, *(("-f", demuxer) if demuxer else ()), "-i", str(src_path),
                *out_args, "-fs", str(max_output), str(dst_path),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                _, stderr = await asyncio.wait_for(proc.communicate(), timeout)
            except TimeoutError:
                raise ConversionError("ffmpeg работал слишком долго — файл слишком длинный или повреждён") from None
            finally:
                if proc.returncode is None:  # таймаут или отмена запроса: процесс не должен остаться висеть
                    proc.kill()
                    await proc.wait()
            if proc.returncode != 0 or not dst_path.exists():
                message = stderr.decode(errors="replace").replace(tmp, "").strip()[-500:]
                raise FFmpegFailed(message or "ffmpeg завершился с ошибкой")
            if dst_path.stat().st_size >= max_output:  # -fs обрезает молча
                raise ConversionError(f"После конвертации файл больше {max_output // (1024 * 1024)} МБ — "
                                      "слишком длинная запись, разделите её на части")
            return dst_path.read_bytes()


async def normalize_cover(data: bytes) -> bytes:
    """Картинка для обложки: JPEG или PNG разумного размера (большие и WEBP/GIF пережимаются в JPEG)."""
    kind = image_type(data)
    if kind is None:
        raise ConversionError("Обложка должна быть картинкой: JPEG, PNG или WEBP")
    if len(data) > MAX_COVER_BYTES:
        raise ConversionError("Обложка больше 10 МБ — выберите картинку поменьше")
    if kind in ("jpeg", "png") and len(data) <= 1_500_000:
        return data
    if not ffmpeg_available():
        if kind in ("jpeg", "png"):
            return data
        raise ConversionError("Пришлите обложку в JPEG или PNG")
    side = COVER_MAX_SIDE
    scale = f"scale='min({side},iw)':'min({side},ih)':force_original_aspect_ratio=decrease"
    try:
        return await _ffmpeg(
            data, ["-vf", scale, "-frames:v", "1", "-q:v", "3"], "cover.jpg", demuxer=_IMAGE_DEMUXERS[kind],
            in_args=("-max_pixels", str(MAX_COVER_PIXELS)), timeout=COVER_TIMEOUT, max_output=MAX_COVER_BYTES,
        )
    except FFmpegFailed as e:
        raise ConversionError("Не получилось прочитать картинку: она повреждена или слишком большая "
                              f"(больше {MAX_COVER_PIXELS // 1_000_000} мегапикселей)") from e


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


UNKNOWN_FORMAT = "Не удалось распознать аудио: пришлите MP3, M4A, FLAC, OGG/Opus, WAV, AIFF, WMA, APE или AAC"


async def convert_to_mp3(data: bytes, fmt: str | None, bitrate: int = 320) -> bytes:
    """Конвертирует аудио формата fmt (см. audio_format) в MP3 через ffmpeg.

    Текстовые теги переносятся, обложку допишет prepare_for_upload.
    """
    args = ["-map", "0:a:0", "-map_metadata", "0", "-codec:a", "libmp3lame", "-b:a", f"{bitrate}k",
            "-id3v2_version", "3"]
    if (demuxer := _AUDIO_DEMUXERS.get(fmt or "")) is not None:
        return await _ffmpeg(data, args, "out.mp3", demuxer=demuxer)
    try:  # формат не опознали: пусть ffmpeg угадает сам, но только среди обычных аудиоформатов
        return await _ffmpeg(data, args, "out.mp3", in_args=("-format_whitelist", _PROBE_FORMATS))
    except FFmpegFailed as e:
        raise ConversionError(UNKNOWN_FORMAT) from e


async def reencode_mp3(data: bytes) -> bytes:
    """Перекодирует файл в обычный MP3 320 kbps, сохраняя теги и обложку (когда Яндекс не принял файл как есть)."""
    meta = read_tags(data)
    converted = await convert_to_mp3(data, audio_format(data) or "mp3")
    return write_mp3_tags(converted, meta)


def _pick(edited: str | None, from_file: str | None) -> str | None:
    """Правка пользователя важнее тега из файла; пустая строка — очистить поле."""
    if edited is None:
        return from_file or None
    return edited.strip() or None


def merge_meta(edit: TrackMeta, tags: TrackMeta, fallback_artist: str | None = None,
               fallback_title: str | None = None) -> TrackMeta:
    """Что окажется в треке: правка важнее тега из файла, а для названия и исполнителя, если пусто и там,
    и там, — запасной вариант (метаданные Telegram или имя файла)."""
    return TrackMeta(
        title=_pick(edit.title, tags.title) or fallback_title or None,
        artist=_pick(edit.artist, tags.artist) or fallback_artist or None,
        album=_pick(edit.album, tags.album),
        year=_pick(edit.year, tags.year),
        cover=None if edit.remove_cover else (edit.cover or tags.cover),
    )


async def prepare_for_upload(
    file_name: str,
    data: bytes,
    *,
    edit: TrackMeta | None = None,
    fallback_artist: str | None = None,
    fallback_title: str | None = None,
) -> tuple[str, bytes, list[str]]:
    """Готовит файл к загрузке в Яндекс Музыку. Возвращает (имя файла, байты, заметки для пользователя).

    - не-MP3 конвертируется в MP3, если есть ffmpeg (обложка из исходного файла сохраняется);
    - правки пользователя (edit) важнее тегов файла, поля без правки остаются как в файле;
    - fallback_* (например, из метаданных Telegram или имени файла) пишутся, только если в файле нет своих.
    """
    edit = edit or TrackMeta()
    notes: list[str] = []
    name = safe_filename(file_name)
    ext = Path(name).suffix.lower()
    original = read_tags(data)
    converted = False
    fmt = audio_format(data)
    # Конвертируем всё, что внутри не MP3, даже если файл называется .mp3. Неопознанный .mp3 оставляем как есть:
    # если Яндекс его не примет, upload_track перекодирует и попробует ещё раз.
    needs_mp3 = fmt != "mp3" and (fmt is not None or ext != ".mp3")

    if needs_mp3:
        if ffmpeg_available():
            data = await convert_to_mp3(data, fmt)
            if ext == ".mp3":
                notes.append(f"внутри файла не MP3, а {FORMAT_NAMES.get(fmt, fmt)} — сконвертирован в MP3")
            else:
                notes.append(f"сконвертирован из {ext or 'неизвестного формата'} в MP3")
            converted = True
        else:
            notes.append(f"{ext or 'файл'} загружен как есть (ffmpeg не установлен, Яндекс надёжнее принимает MP3)")
            if edit.changed:
                notes.append("правки данных трека не применены — для этого нужен ffmpeg")
            return name, data, notes
    if Path(name).suffix != ".mp3":  # в том числе «.MP3»: Яндекс ждёт MP3 с обычным расширением
        name = f"{Path(name).stem}.mp3"

    current = read_tags(data)  # после конвертации текстовые теги уже на месте, а обложки нет
    if converted:  # что mutagen не прочитал в исходнике (например, WebM), мог перенести ffmpeg
        original = TrackMeta(
            title=original.title or current.title, artist=original.artist or current.artist,
            album=original.album or current.album, year=original.year or current.year,
            cover=original.cover or current.cover,
        )
    final = merge_meta(edit, original, fallback_artist, fallback_title)
    if final != current:
        data = write_mp3_tags(data, final)

    if edit.artist is not None or edit.title is not None:
        if final.artist and final.title:
            name = safe_filename(f"{final.artist} - {final.title}") + ".mp3"
            notes.append(f"теги: {final.artist} — {final.title}")
    if edit.album is not None:
        notes.append(f"альбом: {final.album}" if final.album else "альбом убран")
    if edit.year is not None:
        notes.append(f"год: {final.year}" if final.year else "год убран")
    if edit.cover:
        notes.append("новая обложка")
    elif edit.remove_cover:
        notes.append("обложка убрана")
    elif converted and original.cover:
        notes.append("обложка из файла сохранена")
    return name, data, notes
