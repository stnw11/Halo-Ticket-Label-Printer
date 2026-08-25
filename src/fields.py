"""Resolves config/fields.yaml's path syntax against a Halo ticket JSON
payload. router.py reuses resolve_path() for routing.yaml's match_field
so the whole config surface shares one syntax.
"""

from pathlib import Path
from typing import Any

import yaml


def resolve_path(ticket_json: dict, path: str) -> str:
    """Resolve one path against ticket_json.

    - dotted paths walk nested objects: "site.name"
    - a "cf:<name>" prefix looks up a custom field by name in the
      ticket's customfields array (Halo returns custom fields as a list
      of {name, value} objects, not flat keys)
    - a missing/null value resolves to "" so templates can just omit
      that line
    """
    if path.startswith("cf:"):
        return _resolve_custom_field(ticket_json, path[3:])
    return _resolve_dotted_path(ticket_json, path)


def _resolve_dotted_path(obj: Any, path: str) -> str:
    current = obj
    for part in path.split("."):
        if not isinstance(current, dict):
            return ""
        current = current.get(part)
    return _stringify(current)


def _resolve_custom_field(ticket_json: dict, field_name: str) -> str:
    custom_fields = ticket_json.get("customfields") or []
    for cf in custom_fields:
        if isinstance(cf, dict) and cf.get("name") == field_name:
            return _stringify(cf.get("value"))
    return ""


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def resolve_fields(ticket_json: dict, fields_config: dict[str, str | list[str]]) -> dict[str, str]:
    """Resolve every entry in fields_config against ticket_json.

    A value may be a single path, or a list of paths for a compound field
    (e.g. an address assembled from several sub-fields) -- resolved parts
    are joined with ", ", skipping any that come back empty.
    """
    resolved: dict[str, str] = {}
    for var, path_or_paths in fields_config.items():
        if isinstance(path_or_paths, list):
            parts = [resolve_path(ticket_json, p) for p in path_or_paths]
            resolved[var] = ", ".join(p for p in parts if p)
        else:
            resolved[var] = resolve_path(ticket_json, path_or_paths)
    return resolved


def load_fields_config(config_path: str | Path) -> dict[str, str]:
    with open(config_path, "r") as f:
        data = yaml.safe_load(f) or {}
    return data.get("fields") or {}
