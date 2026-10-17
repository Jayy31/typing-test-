"""Change one scalar in a YAML file as text, keeping comments, order and formatting (the PR diff
is exactly one line)."""

import re


def get_value(text: str, path: str) -> str | None:
    loc = _locate(text, path)
    return None if loc is None else loc[1]


def set_value(text: str, path: str, value: float | int | str) -> str:
    loc = _locate(text, path)
    if loc is None:
        raise KeyError(f"{path} not found")
    i, _old = loc
    lines = text.split("\n")
    m = re.match(r"^(\s*[\w.-]+:\s*)([^#\n]*?)(\s*(#.*)?)$", lines[i])
    assert m is not None
    lines[i] = f"{m.group(1)}{_fmt(value)}{m.group(3)}"
    return "\n".join(lines)


def _fmt(v: float | int | str) -> str:
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _locate(text: str, path: str) -> tuple[int, str] | None:
    keys = path.split(".")
    lines = text.split("\n")
    depth, parent_indent = 0, -1
    for i, ln in enumerate(lines):
        s = ln.split("#", 1)[0].rstrip()
        if not s.strip():
            continue
        indent = len(ln) - len(ln.lstrip())
        if indent <= parent_indent and depth > 0:
            return None  # left the parent block without finding the key
        m = re.match(r"^\s*([\w.-]+):\s*(.*)$", s)
        if not m or indent <= parent_indent:
            continue
        if m.group(1) == keys[depth]:
            if depth == len(keys) - 1:
                return i, m.group(2).strip()
            depth += 1
            parent_indent = indent
    return None
