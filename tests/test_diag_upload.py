"""Разбор бандлов нового сайта Яндекс Музыки для поиска API загрузки."""

from bot.diag_upload import chunk_urls_from_html, chunk_urls_from_runtime, find_api_paths, find_snippets

BASE = "https://yastatic-net.ru/s3/music-frontend-static/music/v4.1626.1/_next/static/chunks/"


def test_chunk_urls_from_html_includes_app_router_pages():
    html = (f'<script src="{BASE}webpack-abc123.js"></script>'
            f'<script src="{BASE}app/(main)/playlists/[uuid]/page-9f8e.js" async></script>'
            '<link rel="stylesheet" href="https://yastatic-net.ru/s3/x/_next/static/css/1d39.css">')
    assert chunk_urls_from_html(html) == {
        f"{BASE}webpack-abc123.js", f"{BASE}app/(main)/playlists/[uuid]/page-9f8e.js",
    }


def test_chunk_urls_from_runtime_decodes_all_name_forms():
    runtime = ('a.u=e=>1234===e?"static/chunks/"+e+"-0a1b2c.js":"static/chunks/fixed-name.js"'
               ',"static/chunks/"+(({8290:"ugc-upload"})[e]||e)+"."+({8290:"deadbeef",77:"cafe01"})[e]+".js"')
    assert chunk_urls_from_runtime(runtime, BASE) == {
        f"{BASE}1234-0a1b2c.js", f"{BASE}fixed-name.js",
        f"{BASE}ugc-upload.deadbeef.js", f"{BASE}77.cafe01.js",
    }


def test_find_api_paths_and_snippets():
    js = ('x.get("/loader/upload-url",{params:{kind:e,filename:t}});'
          'const u=`/ugc/tracks/${id}/state`;fetch("/playlists/list");'
          'someOther.upload(avatar);')
    assert find_api_paths(js) == {"/loader/upload-url", "/ugc/tracks/${id}/state"}
    found = find_snippets({BASE + "8290-x.js": js})
    [ugc] = found["ugc"]
    assert "[8290-x.js]" in ugc and "/loader/upload-url" in ugc
    assert found["upload-url"] == [], "тот же участок кода второй раз не показываем"

    far = js + " " * 1000 + 'post("/loader/upload-url2")'
    found = find_snippets({BASE + "8290-x.js": far})
    assert len(found["ugc"]) == 1 and len(found["upload-url"]) == 1
    assert "upload-url2" in found["upload-url"][0]
