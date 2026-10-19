"""Per-role settings for the research agent (config/research_agent.yaml).

Resolution order, last wins: preset (RESEARCH_AGENT_PRESET) -> role_defaults -> roles.<role> ->
env RESEARCH_<ROLE>_<FIELD> (role without the `research_` prefix, e.g. RESEARCH_LIVE_MODEL).
The preset fills `model` and `web`; role_defaults may not override what the preset chose, the
owner's `roles` block and env may. A blank research_top model means: use research_live.
"""

import os
from dataclasses import dataclass, fields
from typing import Any

from app.config import get_settings
from app.yaml_config import load_yaml

ROLES = ("research_files", "research_live", "research_top", "research_review")
PRESET_FIELDS = ("model", "web")


@dataclass(frozen=True)
class RoleConfig:
    role: str
    preset: str
    provider: str
    model: str
    effort: str | None
    web: bool
    max_tool_calls: int
    max_web_searches: int
    max_web_fetches: int
    max_input_tokens: int
    max_output_tokens: int
    max_inr: float
    timeout_s: float

    def models_tag(self) -> dict[str, str]:
        return {"role": self.role, "provider": self.provider, "model": self.model}


def cfg() -> dict[str, Any]:
    return load_yaml("research_agent.yaml")


def preset_name() -> str:
    name = (get_settings().research_agent_preset or cfg()["preset_default"]).strip()
    if name not in cfg()["presets"]:
        raise ValueError(
            f"unknown RESEARCH_AGENT_PRESET {name!r}; one of {sorted(cfg()['presets'])}"
        )
    return name


def _env(role: str, field: str) -> str | None:
    value = os.environ.get(f"RESEARCH_{role.removeprefix('research_').upper()}_{field.upper()}")
    return value if value not in (None, "") else None


def _coerce(field: str, value: Any) -> Any:
    types = {f.name: f.type for f in fields(RoleConfig)}
    t = types[field]
    if value is None:
        return None
    if t in (bool, "bool"):
        return (
            value if isinstance(value, bool) else str(value).lower() in ("1", "true", "yes", "on")
        )
    if t in (int, "int"):
        return int(value)
    if t in (float, "float"):
        return float(value)
    return str(value)


def role_config(role: str, preset: str | None = None) -> RoleConfig:
    if role not in ROLES:
        raise ValueError(f"unknown research role {role}")
    c = cfg()
    preset = preset or preset_name()
    merged: dict[str, Any] = dict(c["role_defaults"][role])
    merged.update({k: v for k, v in (c["presets"][preset].get(role) or {}).items()})
    merged.update((c.get("roles") or {}).get(role) or {})
    for f in fields(RoleConfig):
        if f.name in ("role", "preset"):
            continue
        env = _env(role, f.name)
        if env is not None:
            merged[f.name] = env
    if role == "research_top" and not merged.get("model"):
        live = role_config("research_live", preset)
        return RoleConfig(**{**live.__dict__, "role": role})
    if not merged.get("model"):
        raise RuntimeError(f"no model configured for {role} (preset {preset})")
    out = {
        f.name: _coerce(f.name, merged.get(f.name))
        for f in fields(RoleConfig)
        if f.name not in ("role", "preset")
    }
    out["web"] = bool(out["web"])
    return RoleConfig(role=role, preset=preset, **out)


def top_enabled(preset: str | None = None) -> bool:
    """True when research_top has its own model (else the top candidate uses research_live)."""
    c = cfg()
    preset = preset or preset_name()
    model = (
        _env("research_top", "model")
        or ((c.get("roles") or {}).get("research_top") or {}).get("model")
        or (c["presets"][preset].get("research_top") or {}).get("model")
    )
    return bool(model)


def all_roles(preset: str | None = None) -> dict[str, RoleConfig]:
    return {r: role_config(r, preset) for r in ROLES}
