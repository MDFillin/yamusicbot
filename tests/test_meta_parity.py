"""Одни правила данных трека на сервере (bot/audio.py) и на телефоне (bot/web/static/meta.js).

Примеры — tests/fixtures/meta_cases.json. Меняете правило — добавьте пример туда: проверятся обе реализации.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from bot.audio import TrackMeta, decode_latin1, guess_from_name, merge_meta

ROOT = Path(__file__).parent
CASES = json.loads((ROOT / "fixtures" / "meta_cases.json").read_text("utf-8"))


@pytest.mark.parametrize("case", CASES["guess"], ids=lambda c: c["name"])
def test_guess_from_name(case):
    assert guess_from_name(case["name"]) == (case["artist"], case["title"])


@pytest.mark.parametrize("case", CASES["latin1"], ids=lambda c: c["text"])
def test_decode_latin1(case):
    assert decode_latin1(bytes.fromhex(case["hex"])) == case["text"]


@pytest.mark.parametrize("case", CASES["merge"])
def test_merge_meta(case):
    fb = case["fallback"]
    got = merge_meta(TrackMeta(**case["edit"]), TrackMeta(**case["tags"]), fb.get("artist"), fb.get("title"))
    assert {k: getattr(got, k) for k in ("title", "artist", "album", "year")} == case["expect"]


@pytest.mark.skipif(shutil.which("node") is None, reason="нужен node")
def test_same_rules_in_mini_app():
    result = subprocess.run(["node", str(ROOT / "js" / "check_meta.cjs")], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
