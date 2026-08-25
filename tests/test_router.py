from src.router import resolve_printer_id


def test_resolve_printer_id_uses_matching_rule():
    ticket = {"team": "DPW"}
    config = {"match_field": "team", "rules": {"DPW": "dpw"}, "default_printer": "default"}
    assert resolve_printer_id(ticket, config) == "dpw"


def test_resolve_printer_id_falls_back_to_default_when_no_rule_matches():
    ticket = {"team": "Facilities"}
    config = {"match_field": "team", "rules": {"DPW": "dpw"}, "default_printer": "default"}
    assert resolve_printer_id(ticket, config) == "default"


def test_resolve_printer_id_falls_back_to_default_when_field_missing():
    ticket = {}
    config = {"match_field": "team", "rules": {"DPW": "dpw"}, "default_printer": "default"}
    assert resolve_printer_id(ticket, config) == "default"


def test_resolve_printer_id_with_empty_rules_always_uses_default():
    ticket = {"team": "anything"}
    config = {"match_field": "team", "rules": {}, "default_printer": "default"}
    assert resolve_printer_id(ticket, config) == "default"


def test_resolve_printer_id_supports_custom_field_match():
    ticket = {"customfields": [{"name": "CFSite", "value": "DPW Shop"}]}
    config = {"match_field": "cf:CFSite", "rules": {"DPW Shop": "dpw"}, "default_printer": "default"}
    assert resolve_printer_id(ticket, config) == "dpw"
