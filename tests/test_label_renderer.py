from src.label_renderer import _escape_zpl_text, _format_created_date, render
from src.models import Printer

FIELDS_CONFIG = {
    "ticket_id": "id",
    "created_date": "dateoccurred",
    "organization": "client_name",
    "site": "site_name",
    "end_user": "user_name",
    "site_address": ["delivery_address.line1", "delivery_address.line2"],
}
URL_PATTERN = "https://example.haloitsm.com/tickets/{ticket_id}"


def make_full_ticket(**overrides):
    ticket = {
        "id": "T-1",
        "dateoccurred": "2026-08-24T20:59:39.657",
        "client_name": "Acme Corp",
        "site_name": "Main Office",
        "user_name": "Jane Smith",
        "delivery_address": {"line1": "123 Main St", "line2": "Springfield"},
    }
    ticket.update(overrides)
    return ticket


def make_printer(**overrides):
    defaults = dict(
        id="default",
        name="Test Printer",
        ip="10.0.0.5",
        port=9100,
        dpi=203,
        label_width_in=2.0,
        label_height_in=4.0,
        connect_timeout_seconds=5,
        retries=3,
        qr_magnification=6,
    )
    defaults.update(overrides)
    return Printer(**defaults)


# --- escaping ------------------------------------------------------------


def test_escape_strips_caret_and_tilde():
    assert _escape_zpl_text("^XZ and ~DY") == "XZ and DY"


def test_escape_leaves_normal_text_untouched():
    assert _escape_zpl_text("printer jammed at front desk") == "printer jammed at front desk"


def test_render_sanitizes_injection_attempt_in_ticket_id():
    """Acceptance criterion 5: ticket-derived text containing ^XZ and ~DY
    must print sanitized literal text -- no printer-command injection (no
    extra ^XA/^XZ pair, no stray commands introduced)."""
    ticket = {"id": "T-1^XZ^XA~DY^PQ99", "dateoccurred": "2026-08-24T20:59:39.657"}
    printer = make_printer()
    zpl = render(ticket, printer, qty=1, fields_config=FIELDS_CONFIG, ticket_url_pattern=URL_PATTERN).decode()

    # exactly one label per job: one ^XA...^XZ pair, not reopened by the injected text
    assert zpl.count("^XA") == 1
    assert zpl.count("^XZ") == 1
    # the raw control characters must not appear anywhere in the output
    assert "^XZ^XA" not in zpl
    assert "~DY" not in zpl
    # the sanitized (caret/tilde-stripped) text shows up literally in a ^FD field
    assert "T-1XZXADYPQ99" in zpl


def test_render_sanitizes_injection_attempt_via_qr_url():
    """The QR code's data string is built from the (escaped) ticket_id, so
    an injection attempt there must also come out sanitized."""
    ticket = {"id": "T-1^XZ^XA", "dateoccurred": ""}
    printer = make_printer()
    zpl = render(ticket, printer, qty=1, fields_config=FIELDS_CONFIG, ticket_url_pattern=URL_PATTERN).decode()
    assert zpl.count("^XA") == 1
    assert zpl.count("^XZ") == 1
    assert "https://example.haloitsm.com/tickets/T-1XZXA" in zpl


# --- created_date ----------------------------------------------------------


def test_format_created_date_strips_time_portion():
    assert _format_created_date("2026-08-24T20:59:39.657") == "2026-08-24"


def test_format_created_date_handles_empty_string():
    assert _format_created_date("") == ""


def test_render_includes_created_date():
    ticket = {"id": "T-1", "dateoccurred": "2026-08-24T20:59:39.657"}
    printer = make_printer()
    zpl = render(ticket, printer, qty=1, fields_config=FIELDS_CONFIG, ticket_url_pattern=URL_PATTERN).decode()
    assert "^FD2026-08-24^FS" in zpl


def test_render_omits_created_date_block_when_empty():
    ticket = {"id": "T-1", "dateoccurred": ""}
    printer = make_printer()
    zpl = render(ticket, printer, qty=1, fields_config=FIELDS_CONFIG, ticket_url_pattern=URL_PATTERN).decode()
    # only the ticket_id ^FD field remains; no second (empty) date field
    assert zpl.count("^FD") == 2  # ticket_id + QR code's ^FD, no created_date ^FD


# --- organization / site / end_user / site_address (compound field) -------


def test_render_includes_organization_site_end_user():
    ticket = make_full_ticket()
    printer = make_printer()
    zpl = render(ticket, printer, qty=1, fields_config=FIELDS_CONFIG, ticket_url_pattern=URL_PATTERN).decode()
    assert "^FDAcme Corp^FS" in zpl
    assert "^FDMain Office^FS" in zpl
    assert "^FDJane Smith^FS" in zpl


def test_render_joins_compound_site_address():
    ticket = make_full_ticket()
    printer = make_printer()
    zpl = render(ticket, printer, qty=1, fields_config=FIELDS_CONFIG, ticket_url_pattern=URL_PATTERN).decode()
    assert "^FD123 Main St, Springfield^FS" in zpl


def test_render_omits_site_address_when_all_parts_empty():
    ticket = make_full_ticket(delivery_address={})
    printer = make_printer()
    zpl = render(ticket, printer, qty=1, fields_config=FIELDS_CONFIG, ticket_url_pattern=URL_PATTERN).decode()
    assert "123 Main St" not in zpl
    assert zpl.count("^FD") == 6  # organization, site, end_user, created_date, ticket_id, QR


def test_render_joins_compound_field_skipping_empty_parts():
    ticket = make_full_ticket(delivery_address={"line1": "123 Main St", "line2": ""})
    printer = make_printer()
    zpl = render(ticket, printer, qty=1, fields_config=FIELDS_CONFIG, ticket_url_pattern=URL_PATTERN).decode()
    assert "^FD123 Main St^FS" in zpl


# --- general structure --------------------------------------------------


def test_render_produces_well_formed_label():
    ticket = {"id": "T-1", "dateoccurred": "2026-08-24T20:59:39.657"}
    printer = make_printer()
    zpl = render(ticket, printer, qty=1, fields_config=FIELDS_CONFIG, ticket_url_pattern=URL_PATTERN).decode()
    assert zpl.startswith("^XA")
    assert zpl.strip().endswith("^XZ")


# --- dpi scaling ---------------------------------------------------------


def test_dpi_scaling_multiplier_applied_to_coordinates():
    ticket = {"id": "T-1", "dateoccurred": "2026-08-24T20:59:39.657"}
    printer_203 = make_printer(dpi=203)
    printer_300 = make_printer(dpi=300)

    zpl_203 = render(ticket, printer_203, qty=1, fields_config=FIELDS_CONFIG, ticket_url_pattern=URL_PATTERN).decode()
    zpl_300 = render(ticket, printer_300, qty=1, fields_config=FIELDS_CONFIG, ticket_url_pattern=URL_PATTERN).decode()

    assert "^PW406" in zpl_203
    assert "^LL812" in zpl_203
    assert "^PW600" in zpl_300
    assert "^LL1200" in zpl_300


# --- qty / general structure ----------------------------------------------


def test_render_includes_pq_with_qty():
    ticket = {"id": "T-1", "dateoccurred": "2026-08-24T20:59:39.657"}
    printer = make_printer()
    zpl = render(ticket, printer, qty=7, fields_config=FIELDS_CONFIG, ticket_url_pattern=URL_PATTERN).decode()
    assert "^PQ7" in zpl


def test_render_embeds_qr_code_with_ticket_url():
    ticket = {"id": "T-42", "dateoccurred": ""}
    printer = make_printer()
    zpl = render(ticket, printer, qty=1, fields_config=FIELDS_CONFIG, ticket_url_pattern=URL_PATTERN).decode()
    assert "^BQR,2," in zpl
    assert "https://example.haloitsm.com/tickets/T-42" in zpl
