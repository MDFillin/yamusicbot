"""Объединение и копирование плейлистов.

В свой плейлист (или новый, пустой — так получается копия) дописываются треки из других источников: своих
плейлистов, «Мне нравится», а по ссылке — любого открытого плейлиста или альбома, в том числе плейлиста друга.
Так из двух плейлистов получается общий.

Треки переносятся по их id в Яндекс Музыке, а не через поиск: точно тот же трек, без ошибок поиска и быстро.
Повторы не добавляются — ни те, что уже есть в плейлисте, ни повторы между источниками, — поэтому объединение
можно запускать снова: добавятся только новые треки. Порядок — как в источниках, источники — по очереди.

Загруженные пользователем треки (без альбома) Яндекс даёт добавить только их владельцу, поэтому чужие
загрузки пропускаются, а свои — переносятся.
"""

from __future__ import annotations

from dataclasses import dataclass

from bot.links import parse_link
from bot.sources import SourceNotFoundError, playlist_ref
from bot.ym import YandexMusic

MAX_TRACKS = 10_000  # больше Яндекс в один плейлист не кладёт
MAX_SOURCES = 10


@dataclass(frozen=True)
class Source:
    src: str  # likes | pl | alb
    ref: str = ""  # pl: «uid.kind», alb: id
    title: str = ""

    def json(self) -> dict[str, str]:
        return {"src": self.src, "ref": self.ref, "title": self.title}


@dataclass
class MergeResult:
    title: str
    added: int = 0
    already: int = 0  # уже были в плейлисте или повторялись в источниках
    skipped: int = 0  # чужие загруженные треки
    over_limit: int = 0  # не поместились: в плейлисте Яндекса не больше MAX_TRACKS

    def json(self) -> dict[str, object]:
        return {"title": self.title, "added": self.added, "already": self.already, "skipped": self.skipped,
                "over_limit": self.over_limit}


LIKES = Source("likes", "", "Мне нравится")


async def source_from_link(ym: YandexMusic, text: str) -> Source:
    """Ссылка на плейлист или альбом → источник. SourceNotFoundError — не та ссылка или нет доступа."""
    link = parse_link(text)
    if link is None or link.kind not in ("playlist", "playlist_uuid", "album"):
        raise SourceNotFoundError("Нужна ссылка на плейлист или альбом в Яндекс Музыке")
    if link.kind == "album":
        album = await ym.get_album(link.album)
        if album is None:
            raise SourceNotFoundError("Альбом не найден")
        artists = ", ".join(a.name for a in album.artists or [] if a.name)
        return Source("alb", str(album.id), f"{artists} — {album.title}" if artists else album.title)
    if link.kind == "playlist_uuid":
        playlist = await ym.get_playlist_by_uuid(link.uuid)
    else:
        playlist = await ym.get_playlist(link.playlist_kind, link.owner)
    if playlist is None:
        raise SourceNotFoundError("Плейлист не найден — возможно, он закрытый: попросите владельца открыть его")
    owner = (playlist.owner.name or playlist.owner.login) if playlist.owner else None
    title = playlist.title if not owner or playlist_ref(playlist).partition(".")[0] == str(ym.uid) \
        else f"{playlist.title} ({owner})"
    return Source("pl", playlist_ref(playlist), title)


Track = tuple[str, str | None]  # (id трека, id альбома или None у загруженных)


async def source_tracks(ym: YandexMusic, source: Source) -> tuple[list[Track], int]:
    """Треки источника по порядку и сколько чужих загрузок в нём пропущено."""
    if source.src == "likes":
        likes = await ym.client.users_likes_tracks()
        shorts = (likes.tracks if likes else None) or []
        return [(str(s.id), str(s.album_id) if s.album_id else None) for s in shorts], 0
    if source.src == "pl":
        owner, _, kind = source.ref.partition(".")
        playlist = await ym.get_playlist(kind, owner)
        if playlist is None:
            raise SourceNotFoundError(f"Плейлист «{source.title}» не найден")
        mine = owner == str(ym.uid)
        shorts = playlist.tracks or []
        tracks = [(str(s.id), str(s.album_id) if s.album_id else None) for s in shorts if s.album_id or mine]
        return tracks, len(shorts) - len(tracks)
    if source.src == "alb":
        album = await ym.get_album(source.ref)
        if album is None:
            raise SourceNotFoundError(f"Альбом «{source.title}» не найден")
        return [(str(t.id), source.ref) for volume in album.volumes or [] for t in volume], 0
    raise SourceNotFoundError(f"Неизвестный источник {source.src}")


async def merge_into(ym: YandexMusic, kind: int, sources: list[Source]) -> MergeResult:
    """Дописывает в свой плейлист kind треки из sources, без повторов."""
    target = await ym.get_playlist(kind)
    if target is None:
        raise SourceNotFoundError("Плейлист, куда добавлять, не найден — возможно, он удалён")
    result = MergeResult(target.title or "")
    seen = {str(s.id) for s in target.tracks or []}
    room = MAX_TRACKS - len(target.tracks or [])
    items: list[Track] = []
    for source in sources[:MAX_SOURCES]:
        if source.src == "pl" and source.ref == f"{ym.uid}.{kind}":
            continue  # сам в себя
        tracks, skipped = await source_tracks(ym, source)
        result.skipped += skipped
        for track_id, album_id in tracks:
            if track_id in seen:
                result.already += 1
                continue
            seen.add(track_id)
            if len(items) >= room:
                result.over_limit += 1
                continue
            items.append((track_id, album_id))
    if items:
        await ym.append_tracks(kind, items, target)
    result.added = len(items)
    return result
