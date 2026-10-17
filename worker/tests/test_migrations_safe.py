"""Migrations must be add-only so update.sh can always roll back to the previous image: no drops
or renames in upgrade(). A removal ships in a LATER release than the code that stopped using it,
with the marker comment `# safe-removal: <why>` on the line."""

import ast
import re
from pathlib import Path

VERSIONS = Path(__file__).resolve().parents[1] / "app/db/migrations/versions"
OPS = {"drop_table", "drop_column", "drop_index", "drop_constraint", "rename_table"}
SQL = re.compile(
    r"\b(DROP\s+(TABLE|COLUMN)|RENAME\s+(TO|COLUMN)|ALTER\s+COLUMN\s+\w+\s+TYPE)\b", re.I
)
LEGACY = {
    "0001",
    "0002",
    "0003",
    "0004",
    "0005",
    "0006",
    "0007",
    "0008",
    "0009",
    "0010",
    "0011",
    "0012",
    "0013",
}  # shipped before go-live (nothing to roll back to yet)


def destructive(src: str) -> list[str]:
    lines = src.splitlines()
    found = []
    for fn in ast.walk(ast.parse(src)):
        if not (isinstance(fn, ast.FunctionDef) and fn.name == "upgrade"):
            continue
        for node in ast.walk(fn):
            bad = None
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in OPS:
                    bad = node.func.attr
                elif node.func.attr == "alter_column" and any(
                    k.arg in ("new_column_name", "type_") for k in node.keywords
                ):
                    bad = "alter_column rename/type"
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and SQL.search(node.value)
            ):
                bad = "SQL " + SQL.search(node.value).group(0)
            if bad and "safe-removal:" not in lines[node.lineno - 1]:
                found.append(f"line {node.lineno}: {bad}")
    return found


def test_upgrades_are_add_only() -> None:
    problems = {
        f.name: d
        for f in sorted(VERSIONS.glob("*.py"))
        if f.name[:4] not in LEGACY and (d := destructive(f.read_text()))
    }
    assert not problems, f"destructive migration ops (see module docstring): {problems}"


def test_checker_catches_drops_and_allows_marker() -> None:
    bad = 'def upgrade():\n    op.drop_column("t", "c")\n    op.execute("ALTER TABLE t DROP COLUMN x")\n'
    assert len(destructive(bad)) == 2
    ok = 'def upgrade():\n    op.drop_column("t", "c")  # safe-removal: unused since 0014\n'
    assert destructive(ok) == []
    assert destructive('def downgrade():\n    op.drop_table("t")\n') == []
