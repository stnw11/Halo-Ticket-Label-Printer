"""Renders a Halo ticket + printer config into ZPL bytes, using
config/labels/default.zpl.j2.

Label layout:
- Left side (low y): organization, site, end_user, site_address,
  created_date, stacked top-to-bottom (high x to low x). site_address is
  reserved but unmapped by default (see config/fields.yaml) -- its row is
  left blank rather than reflowing the other four.
- Right side (high y): a QR code linking to the ticket, with ticket_id
  below it (lower x than the QR).

All ticket-derived text is escaped (^ and ~ stripped -- ZPL's command
prefix characters, which the printer scans for anywhere in the data
stream, not just outside ^FD blocks).

Font sizes and ^FO positions are dots at 203 dpi and are scaled by
dpi / 203 so the same template works on a 300 dpi printer.
"""

from pathlib import Path

import jinja2

from .fields import resolve_fields
from .models import Printer

DEFAULT_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "config" / "labels"
DEFAULT_TEMPLATE_NAME = "default.zpl.j2"

_BASE_DPI = 203

# ^BQ's magnification factor is roughly linear in dots per unit, but its
# real footprint also depends on the encoded data's length (QR version).
# This constant was calibrated empirically against a real printout
# (magnification 6 measured ~1in / 203 dots square) so the QR can be
# placed without running off the printable canvas. Re-verify against a
# real printout if your ticket_url_pattern's length changes significantly.
_QR_DOTS_PER_MAGNIFICATION_UNIT_AT_203DPI = 203 / 6


def _escape_zpl_text(text: str) -> str:
    """Strip ZPL's command-prefix characters from ticket-derived text so
    it can't break out of a ^FD field or inject printer commands
    (acceptance criterion 5)."""
    return text.replace("^", "").replace("~", "")


def _format_created_date(raw_value: str) -> str:
    """Halo's dateoccurred is an ISO timestamp (e.g.
    "2026-08-24T20:59:39.657"); the label shows date only."""
    if not raw_value:
        return ""
    return raw_value.split("T", 1)[0]


def render(
    ticket_json: dict,
    printer: Printer,
    qty: int,
    fields_config: dict[str, str],
    ticket_url_pattern: str,
    template_dir: Path = DEFAULT_TEMPLATE_DIR,
    template_name: str = DEFAULT_TEMPLATE_NAME,
) -> bytes:
    scale = printer.dpi / _BASE_DPI
    px_width = round(printer.label_width_in * printer.dpi)
    px_height = round(printer.label_height_in * printer.dpi)
    margin = round(20 * scale)

    resolved = resolve_fields(ticket_json, fields_config)
    ticket_id = _escape_zpl_text(resolved.get("ticket_id", ""))
    created_date = _escape_zpl_text(_format_created_date(resolved.get("created_date", "")))
    organization = _escape_zpl_text(resolved.get("organization", ""))
    site = _escape_zpl_text(resolved.get("site", ""))
    end_user = _escape_zpl_text(resolved.get("end_user", ""))
    site_address = _escape_zpl_text(resolved.get("site_address", ""))

    qr_url = ticket_url_pattern.format(ticket_id=ticket_id)
    qr_data = _escape_zpl_text(qr_url)

    # Both ^BQ (QR) and rotated ^A0R text fields grow in +x/+y from their
    # ^FO anchor -- every anchor below is therefore the *near* edge of its
    # element, not the far one.
    qr_magnification = max(1, min(10, round(printer.qr_magnification * scale)))
    qr_size_dots = round(qr_magnification * _QR_DOTS_PER_MAGNIFICATION_UNIT_AT_203DPI * scale)
    qr_x = px_width - qr_size_dots - margin
    qr_y = px_height - qr_size_dots - margin

    # Left side: 5 stacked lines (organization/site/end_user/site_address/
    # created_date), evenly spaced top-to-bottom across the short axis.
    # row_x[i] is each row's near (low-x) edge; since text grows toward
    # +x, its far edge lands at row_x[i] + info_font.
    info_font = round(34 * scale)
    left_y = round(40 * scale)
    row_height = (px_width - 2 * margin) / 5
    row_x = [round(px_width - margin - i * row_height - info_font) for i in range(5)]

    # Ticket id: larger and wider-than-tall for a bolder look, right-
    # justified within a block spanning the QR's own width so it lands
    # flush with the QR's far edge regardless of how many digits the
    # ticket number has.
    ticket_id_font_height = round(65 * scale)
    ticket_id_font_width = round(80 * scale)

    context: dict = {
        "px_width": px_width,
        "px_height": px_height,
        "qty": qty,
        "organization": organization,
        "organization_x": row_x[0],
        "organization_y": left_y,
        "organization_font": info_font,
        "site": site,
        "site_x": row_x[1],
        "site_y": left_y,
        "site_font": info_font,
        "end_user": end_user,
        "end_user_x": row_x[2],
        "end_user_y": left_y,
        "end_user_font": info_font,
        "site_address": site_address,
        "site_address_x": row_x[3],
        "site_address_y": left_y,
        "site_address_font": info_font,
        "created_date": created_date,
        "created_date_x": row_x[4],
        "created_date_y": left_y,
        "created_date_font": info_font,
        "ticket_id": ticket_id,
        "ticket_id_x": qr_x - margin - ticket_id_font_height,
        "ticket_id_y": qr_y,
        "ticket_id_font_height": ticket_id_font_height,
        "ticket_id_font_width": ticket_id_font_width,
        "ticket_id_block_width": qr_size_dots,
        "qr_data": qr_data,
        "qr_x": qr_x,
        "qr_y": qr_y,
        "qr_magnification": qr_magnification,
    }

    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(template_dir)),
        autoescape=False,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = env.get_template(template_name)
    zpl_text = template.render(**context)
    return zpl_text.encode("utf-8")
