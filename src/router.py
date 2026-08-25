"""Maps a ticket to a printer_id using config/routing.yaml. Reuses
fields.py's resolve_path() so match_field shares the same dotted-path /
"cf:" syntax as fields.yaml -- agents never pick a printer; this is
entirely config-driven.
"""

from pathlib import Path

import yaml

from .fields import resolve_path


def load_routing_config(config_path: str | Path) -> dict:
    with open(config_path, "r") as f:
        return yaml.safe_load(f) or {}


def resolve_printer_id(ticket_json: dict, routing_config: dict) -> str:
    match_field = routing_config.get("match_field")
    rules = routing_config.get("rules") or {}
    default_printer = routing_config.get("default_printer")

    if match_field:
        value = resolve_path(ticket_json, match_field)
        if value in rules:
            return rules[value]
    return default_printer
