from src.fields import resolve_fields, resolve_path


def test_resolve_simple_path():
    ticket = {"id": "T-123"}
    assert resolve_path(ticket, "id") == "T-123"


def test_resolve_dotted_path():
    ticket = {"site": {"name": "City Hall"}}
    assert resolve_path(ticket, "site.name") == "City Hall"


def test_resolve_missing_dotted_path_returns_empty_string():
    ticket = {"site": {}}
    assert resolve_path(ticket, "site.name") == ""
    assert resolve_path(ticket, "does.not.exist") == ""


def test_resolve_null_value_returns_empty_string():
    ticket = {"summary": None}
    assert resolve_path(ticket, "summary") == ""


def test_resolve_custom_field_by_name():
    ticket = {
        "customfields": [
            {"name": "CFLabelPrintQty", "value": 2},
            {"name": "CFSite", "value": "DPW"},
        ]
    }
    assert resolve_path(ticket, "cf:CFSite") == "DPW"
    assert resolve_path(ticket, "cf:CFLabelPrintQty") == "2"


def test_resolve_missing_custom_field_returns_empty_string():
    ticket = {"customfields": [{"name": "Other", "value": "x"}]}
    assert resolve_path(ticket, "cf:NotThere") == ""


def test_resolve_custom_field_with_no_customfields_array():
    assert resolve_path({}, "cf:Anything") == ""


def test_resolve_fields_maps_multiple_variables():
    ticket = {"id": "T-1", "summary": "printer jammed"}
    config = {"ticket_id": "id", "summary": "summary"}
    assert resolve_fields(ticket, config) == {"ticket_id": "T-1", "summary": "printer jammed"}


def test_resolve_fields_compound_field_joins_parts():
    ticket = {"address": {"line1": "123 Main St", "line2": "Springfield"}}
    config = {"full_address": ["address.line1", "address.line2"]}
    assert resolve_fields(ticket, config) == {"full_address": "123 Main St, Springfield"}


def test_resolve_fields_compound_field_skips_empty_parts():
    ticket = {"address": {"line1": "123 Main St", "line2": ""}}
    config = {"full_address": ["address.line1", "address.line2", "address.line3"]}
    assert resolve_fields(ticket, config) == {"full_address": "123 Main St"}


def test_resolve_fields_compound_field_all_empty_yields_empty_string():
    ticket = {"address": {}}
    config = {"full_address": ["address.line1", "address.line2"]}
    assert resolve_fields(ticket, config) == {"full_address": ""}
