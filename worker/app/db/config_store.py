"""Tiny key/value helpers over the `config` table (runtime state and settings)."""

from typing import Any

from sqlalchemy.orm import Session

from app.db.models import Config


def get_config(s: Session, key: str, default: Any = None) -> Any:
    row = s.get(Config, key)
    return default if row is None else row.value_json


def set_config(s: Session, key: str, value: Any) -> None:
    row = s.get(Config, key)
    if row is None:
        s.add(Config(key=key, value_json=value))
    else:
        row.value_json = value
