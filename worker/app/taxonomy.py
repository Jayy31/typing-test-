"""Shared event/theme taxonomy (config/taxonomy.yaml), used by live and historical tagging."""

from app.yaml_config import load_yaml


def version() -> str:
    return str(load_yaml("taxonomy.yaml")["version"])


def event_types() -> list[str]:
    return list(load_yaml("taxonomy.yaml")["event_types"])


def theme_keywords() -> dict[str, list[str]]:
    return {k: [w.lower() for w in v] for k, v in load_yaml("taxonomy.yaml")["themes"].items()}


def themes() -> list[str]:
    return list(load_yaml("taxonomy.yaml")["themes"])


def ambiguous_aliases() -> set[str]:
    return {a.lower() for a in load_yaml("taxonomy.yaml").get("ambiguous_aliases", [])}


def prompt_block() -> str:
    """Taxonomy rendered for prompts, so prompts never hard-code labels."""
    cfg = load_yaml("taxonomy.yaml")
    ev = "\n".join(f"- {k}: {v}" for k, v in cfg["event_types"].items())
    return f"Event types (taxonomy {cfg['version']}):\n{ev}\nThemes: {', '.join(cfg['themes'])}"


def structured_types() -> dict[str, dict[str, str]]:
    """Structured event subtypes (tx2) -> {parent, description}."""
    return dict(load_yaml("taxonomy.yaml").get("structured_event_types") or {})


def parent(event_type: str | None) -> str | None:
    """News event type a structured subtype belongs to (the type itself if not structured)."""
    if event_type is None:
        return None
    return structured_types().get(event_type, {}).get("parent", event_type)
