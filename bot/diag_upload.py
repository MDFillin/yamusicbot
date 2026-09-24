"""Ищет в коде нового сайта music.yandex.ru, как он загружает свои треки (UGC).

Старый адрес music.yandex.ru/handlers/ugc-upload.jsx Яндекс убрал вместе со старым сайтом.
Скрипт скачивает публичные JS-файлы сайта и выписывает места, связанные с загрузкой.
Токены не нужны и не используются, ничего никуда не загружается.

Запуск на сервере (в папке бота):
    docker compose run --rm bot python -m bot.diag_upload
Результат печатается и сохраняется в data/upload_api.txt.
"""

from __future__ import annotations

import os
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")
PAGES = ("https://music.yandex.ru/", "https://music.yandex.ru/collection")

# Что ищем: пути API и куски кода рядом с этими словами. Порядок = приоритет в отчёте.
KEYWORDS = ("ugc", "post-target", "postTarget", "upload-url", "uploadUrl", "loader/", "upload")
PATH_RE = re.compile(r"""["'`](/[\w\-/.{}$:]*(?:ugc|upload|loader)[\w\-/.{}$:]*)["'`]""", re.I)
CONTEXT = 220
MAX_SNIPPETS = {"ugc": 30, "upload": 12}
DEFAULT_MAX = 10


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ru"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", "replace")


def chunk_urls_from_html(html: str) -> set[str]:
    return set(re.findall(r"https://[^\"'\s]+?/_next/static/chunks/[\w./()\[\]@%-]+\.js", html))


def chunk_urls_from_runtime(runtime_js: str, base: str) -> set[str]:
    """Имена лениво подгружаемых чанков из функции a.u() рантайма webpack; base — …/_next/static/chunks/."""
    urls = {base + name for name in re.findall(r'"static/chunks/([^"]+\.js)"', runtime_js)}
    for cid, hash_ in re.findall(r'(\d+)===e\?"static/chunks/"\+e\+"-([a-f0-9]+)\.js"', runtime_js):
        urls.add(f"{base}{cid}-{hash_}.js")
    tables = re.findall(r"\(\{([^}]+)\}\)\[e\]", runtime_js)
    if len(tables) >= 2:
        prefixes = dict(re.findall(r'(\d+):"([\w-]+)"', tables[-2]))
        suffixes = dict(re.findall(r'(\d+):"([a-f0-9]+)"', tables[-1]))
        for cid, suffix in suffixes.items():
            urls.add(f"{base}{prefixes.get(cid, cid)}.{suffix}.js")
    return urls


def find_api_paths(js: str) -> set[str]:
    return {m.group(1) for m in PATH_RE.finditer(js) if len(m.group(1)) < 120}


def find_snippets(sources: dict[str, str]) -> dict[str, list[str]]:
    """Куски кода вокруг ключевых слов; один и тот же участок файла не показываем дважды."""
    found: dict[str, list[str]] = {k: [] for k in KEYWORDS}
    shown: dict[str, list[tuple[int, int]]] = {}
    for name, js in sources.items():
        short = name.rsplit("/", 1)[-1]
        windows = shown.setdefault(name, [])
        for keyword in KEYWORDS:
            limit = MAX_SNIPPETS.get(keyword, DEFAULT_MAX)
            for m in re.finditer(re.escape(keyword), js, flags=re.I):
                if len(found[keyword]) >= limit:
                    break
                if any(a <= m.start() < b for a, b in windows):
                    continue
                a, b = max(0, m.start() - CONTEXT), m.end() + CONTEXT
                windows.append((a, b))
                found[keyword].append(f"[{short}] …{js[a:b].replace(chr(10), ' ')}…")
    return found


def main() -> None:
    htmls = []
    for page in PAGES:
        try:
            htmls.append(fetch(page))
        except Exception as e:
            print(f"Не удалось открыть {page}: {e}", file=sys.stderr)
    if not htmls:
        sys.exit("Сайт music.yandex.ru недоступен с этого сервера.")
    html = "\n".join(htmls)
    version = re.search(r"music-frontend-static/music/(v[\d.]+)/", html)

    urls = set().union(*(chunk_urls_from_html(h) for h in htmls))
    runtime = next((u for u in urls if "/webpack-" in u), None)
    if runtime:
        base = runtime.rsplit("/", 1)[0] + "/"
        try:
            urls |= chunk_urls_from_runtime(fetch(runtime), base)
        except Exception as e:
            print(f"Не удалось разобрать {runtime}: {e}", file=sys.stderr)
    print(f"Версия сайта: {version.group(1) if version else '?'}; JS-файлов: {len(urls)}. Скачиваю…", file=sys.stderr)

    def get(url: str) -> tuple[str, str | None]:
        try:
            return url, fetch(url)
        except Exception:
            return url, None

    with ThreadPoolExecutor(max_workers=8) as pool:
        sources = {u: js for u, js in pool.map(get, sorted(urls)) if js}

    paths = sorted(set().union(*(find_api_paths(js) for js in sources.values())))
    snippets = find_snippets(sources)

    lines = [
        f"Версия сайта: {version.group(1) if version else '?'}",
        f"JS-файлов: скачано {len(sources)} из {len(urls)}",
        "",
        "== Пути API со словами ugc/upload/loader ==",
        *(paths or ["(не найдено)"]),
    ]
    for keyword in KEYWORDS:
        if snippets[keyword]:
            lines += ["", f"== Код рядом с «{keyword}» ==", *snippets[keyword]]
    report = "\n".join(lines)

    out = Path(os.getenv("DATA_DIR", "data")) / "upload_api.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, "utf-8")
    print(report)
    print(f"\nСохранено в {out} — пришлите этот файл разработчику.", file=sys.stderr)


if __name__ == "__main__":
    main()
