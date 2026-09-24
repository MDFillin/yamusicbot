"""Запуск туннеля: токен из .env должен дойти до fxtunnel флагом --token, заготовки — отсекаться."""

import os
import subprocess
from pathlib import Path

import pytest

ENTRYPOINT = Path(__file__).resolve().parent.parent / "tunnel" / "entrypoint.sh"
TOKEN = "sk_fxtunnel_" + "0123456789abcdef" * 3


@pytest.fixture
def run(tmp_path):
    """Запускает entrypoint.sh с поддельным fxtunnel, который печатает свои аргументы."""
    fake = tmp_path / "fxtunnel"
    fake.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
    fake.chmod(0o755)

    def _run(token: str | None) -> subprocess.CompletedProcess:
        env = {"PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}"}
        if token is not None:
            env["FXTUNNEL_TOKEN"] = token
        return subprocess.run(["sh", str(ENTRYPOINT), "http", "8080", "--domain", "my-music"],
                              env=env, capture_output=True, text=True)

    return _run


def test_token_is_passed_as_flag(run):
    result = run(TOKEN)
    assert result.returncode == 0, result.stderr
    assert result.stdout.split("\n")[:-1] == ["http", "8080", "--domain", "my-music", "--token", TOKEN]
    assert result.stderr == ""


@pytest.mark.parametrize("token, reason", [
    (None, "не задан"),
    ("", "не задан"),
    ("fxtunnel_abc", "начинаться с sk_"),
    ("sk_ТВОЙ_ТОКЕН", "лишние символы"),
    ("sk_fxtunnel_0123…", "лишние символы"),
    (f'"{TOKEN}"', "начинаться с sk_"),
    (f"{TOKEN} ", "лишние символы"),
])
def test_bad_token_stops_before_connecting(run, token, reason):
    result = run(token)
    assert result.returncode == 1
    assert result.stdout == "", "fxtunnel не должен запускаться"
    assert reason in result.stderr
    assert "sk_fxtunnel_" in result.stderr and TOKEN not in result.stderr


def test_unusual_length_only_warns(run):
    result = run(TOKEN[:-4])
    assert result.returncode == 0
    assert "--token" in result.stdout
    assert "длиной 56" in result.stderr
