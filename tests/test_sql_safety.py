"""Защита от SQL-инъекций.

1. По исходникам: текст каждого запроса — литерал или собран только из известных безопасных фрагментов,
   значения идут параметрами. Новый запрос вида f"... {что-то}" с чужими данными уронит этот тест.
2. Второй рубеж: даже внедрённый SQL не может подключить файл, поменять схему или настройки, загрузить расширение.
3. Попытки инъекций через настоящие входы: поиск людей, настройки, данные треков, админ-API.
"""

from __future__ import annotations

import ast
import sqlite3
from pathlib import Path

import pytest

from bot.storage import Storage

BOT = Path(__file__).resolve().parent.parent / "bot"
SQL_CALLS = {"execute", "executemany", "executescript", "_one", "_all", "_q", "_users"}
# Фрагменты, которые разрешено вставлять в текст запроса, и как каждый обязан быть получен.
SAFE_NAMES = {"marks", "where", "plays", "SCHEMA"}
# Обёртки Storage, которые лишь передают дальше свой аргумент sql: проверяются места, где их вызывают.
PASS_THROUGH = {"_one", "_all", "_q"}

PAYLOADS = [
    "' OR '1'='1", "1; DROP TABLE users; --", "x') UNION SELECT token, login FROM accounts --",
    "\\' OR 1=1 --", "%", "_", "Robert'); DELETE FROM accounts; --", "\x00", "ё' || (SELECT token FROM accounts) || '",
]


def _is_safe(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return True
    if isinstance(node, ast.JoinedStr):
        return all(isinstance(v, ast.Constant) or (isinstance(v, ast.FormattedValue) and _is_fragment(v.value))
                   for v in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _is_safe(node.left) and _is_safe(node.right)
    return _is_fragment(node)


def _is_fragment(node: ast.AST) -> bool:
    if isinstance(node, ast.Name):
        return node.id in SAFE_NAMES
    return isinstance(node, ast.Attribute) and node.attr == "_PLAYS"  # константа класса Storage


def _sources():
    for path in sorted(BOT.rglob("*.py")):
        yield path, ast.parse(path.read_text("utf-8"), str(path))


def _calls(tree: ast.AST):
    """SQL-вызовы и имя функции, внутри которой каждый стоит (для обёрток PASS_THROUGH)."""
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in SQL_CALLS \
                and node.args:
            outer = parents.get(node)
            while outer is not None and not isinstance(outer, (ast.FunctionDef, ast.AsyncFunctionDef)):
                outer = parents.get(outer)
            yield node, outer is not None and outer.name in PASS_THROUGH


def test_sql_text_is_never_built_from_data():
    bad = []
    for path, tree in _sources():
        for node, wrapper in _calls(tree):
            arg = node.args[0]
            if wrapper and isinstance(arg, ast.Name) and arg.id == "sql":
                continue
            if not _is_safe(arg):
                bad.append(f"{path.relative_to(BOT.parent)}:{node.lineno}: {ast.unparse(arg)[:80]}")
    assert not bad, "SQL собирается из данных — передавайте значения параметрами (?):\n" + "\n".join(bad)


def test_safe_fragments_come_only_from_safe_sources():
    """marks — только «?,?,?», where — только из _user_filter, plays — только константа _PLAYS."""
    allowed = {
        "marks": lambda v: ast.unparse(v).startswith("','.join('?' *"),
        "where": lambda v: ast.unparse(v).startswith("self._user_filter("),
        "plays": lambda v: ast.unparse(v) == "self._PLAYS",
        "SCHEMA": lambda v: isinstance(v, ast.Constant) and isinstance(v.value, str),
    }
    seen = set()
    for path, tree in _sources():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                names = [target] if isinstance(target, ast.Name) else getattr(target, "elts", [])
                for name in names:
                    if isinstance(name, ast.Name) and name.id in allowed and path.name == "storage.py":
                        seen.add(name.id)
                        assert allowed[name.id](node.value), f"{path.name}:{node.lineno}: {ast.unparse(node)}"
    assert seen == set(allowed)


@pytest.fixture
def store(tmp_path):
    s = Storage(tmp_path / "bot.db")
    s.set_account(1, "secret-token", "me")
    s.touch_user(1, "Андрей", None, "andrey", "2026-09-25")
    s.touch_user(2, "100% Боб", None, "bob_", "2026-09-25")
    yield s
    s.close()


def test_user_filter_keeps_input_out_of_sql():
    """Текст условия один и тот же, что бы ни искали: поисковая строка уходит только параметром."""
    for status in ("all", "connected", "banned", "blocked", "'; DROP TABLE users; --"):
        expected, _ = Storage._user_filter("abc", status)
        for payload in PAYLOADS:
            where, args = Storage._user_filter(payload, status)
            assert where == expected
            assert all(payload.strip().lower().replace("\\", "") in a.replace("\\", "") for a in args
                       if payload.strip() not in ("%", "_", "\\' OR 1=1 --"))
    assert Storage._user_filter("'; x", "'; DROP TABLE users; --")[0] == Storage._user_filter("'; x", "all")[0], \
        "неизвестный фильтр — просто «все», а не кусок SQL"


@pytest.mark.parametrize("payload", PAYLOADS)
def test_injection_payloads_are_just_text(store, payload):
    assert {u["id"] for u in store.list_users(payload)} <= {1, 2}
    assert isinstance(store.count_users(payload, "connected"), int)
    store.set_setting(1, payload, payload)
    assert store.get_setting(1, payload) == payload
    store.save_track_meta("1", payload, payload, payload, payload, 1000, payload, [(payload, payload)])
    assert store.track_info(["1"]) == {"1": (payload, [(payload, payload)])}
    store.add_play(1, 1, "2026-09-25", payload, payload, 1000, "live")
    assert store.listening_stats(1, "2026-09-25", "2026-09-25")["plays"] >= 1
    # Всё на месте: ни таблицы, ни токен не пострадали.
    assert store.get_account(1) == ("secret-token", "me") and store.count_users() == 2


def test_like_wildcards_are_literal(store):
    assert [u["id"] for u in store.list_users("%")] == [2], "% ищется как символ, а не «всё подряд»"
    assert [u["id"] for u in store.list_users("_")] == [2]
    assert store.list_users("' OR '1'='1") == []


@pytest.mark.parametrize("sql", [
    "ATTACH DATABASE '{tmp}/evil.db' AS evil",
    "DROP TABLE users",
    "CREATE TABLE evil (x)",
    "CREATE TRIGGER t AFTER INSERT ON users BEGIN DELETE FROM accounts; END",
    "ALTER TABLE users ADD COLUMN evil TEXT",
    "PRAGMA writable_schema = 1",
    "PRAGMA journal_mode = DELETE",
    "SELECT load_extension('/tmp/evil.so')",
    "SELECT fts3_tokenizer('simple')",
])
def test_injected_sql_cannot_escalate(store, sql, tmp_path):
    with pytest.raises(sqlite3.DatabaseError):
        store._db.execute(sql.format(tmp=tmp_path))
    assert not (tmp_path / "evil.db").exists(), "файл на диске не создан"
    assert store.get_account(1) == ("secret-token", "me")


def test_one_statement_per_call(store):
    with pytest.raises(sqlite3.ProgrammingError):
        store._db.execute("SELECT 1; DELETE FROM accounts")
    assert store.get_account(1) is not None


def test_normal_work_still_allowed(store, tmp_path):
    assert store._db.execute("PRAGMA journal_mode").fetchone()[0] == "wal", "читать настройки можно"
    store.add_listens(1, [("2026-09-25", "1", "none")])
    store.delete_listens(1)
    store.backup(tmp_path / "copy.db")
    copy = sqlite3.connect(tmp_path / "copy.db")
    assert copy.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 2
    copy.close()
