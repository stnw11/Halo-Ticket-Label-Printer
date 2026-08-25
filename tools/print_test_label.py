"""Send a sample landscape label straight to a printer, no Halo needed.

Proves the network path and the label orientation end-to-end without
touching Halo at all. Run from the repo root:

    python -m tools.print_test_label --printer default --ticket-id TEST-1 --qty 1
"""

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from src.env import load_dotenv
from src.label_renderer import render as render_label
from src.models import Printer
from src.printer_client import load_printers, send

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "printers.yaml"
DEFAULT_FIELDS_CONFIG_PATH = REPO_ROOT / "config" / "fields.yaml"


def build_test_zpl(printer: Printer, ticket_id: str, qty: int) -> bytes:
    """A minimal hardcoded ZPL string: landscape orientation (^PW/^LL sized
    to the label's short/long edge, text rotated via ^A0R), scaled by
    dpi/203 so it also works on a 300 dpi printer.
    """
    scale = printer.dpi / 203
    px_width = round(printer.label_width_in * printer.dpi)
    px_height = round(printer.label_height_in * printer.dpi)
    font_size = round(60 * scale)
    x = round(300 * scale)
    y = round(40 * scale)

    zpl = (
        "^XA\n"
        f"^PW{px_width}\n"
        f"^LL{px_height}\n"
        "^CI28\n"
        f"^FO{x},{y}^A0R,{font_size},{font_size}^FD{ticket_id}^FS\n"
        f"^PQ{qty}\n"
        "^XZ\n"
    )
    return zpl.encode("utf-8")


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--printer", default="default", help="printer id from config/printers.yaml")
    parser.add_argument("--ticket-id", default="TEST-1", help="text to print as the ticket id field")
    parser.add_argument(
        "--created-date",
        default=None,
        help="ISO timestamp for the created_date field (full-template mode only); defaults to now",
    )
    parser.add_argument("--qty", type=int, default=1, help="number of copies")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="path to printers.yaml")
    parser.add_argument("--fields-config", default=str(DEFAULT_FIELDS_CONFIG_PATH), help="path to fields.yaml")
    parser.add_argument(
        "--minimal",
        action="store_true",
        help="use a minimal hand-rolled ZPL string instead of the full "
        "config/labels/default.zpl.j2 template (useful for isolating "
        "network/orientation issues from template issues)",
    )
    args = parser.parse_args(argv)

    printers = load_printers(args.config)
    if args.printer not in printers:
        known = ", ".join(printers) or "none"
        print(f"error: printer '{args.printer}' not found in {args.config} (known: {known})", file=sys.stderr)
        return 1
    printer = printers[args.printer]
    if printer.ip == "REPLACE_ME":
        print(f"error: printer '{args.printer}' has no IP configured yet in {args.config}", file=sys.stderr)
        return 1

    if args.minimal:
        zpl = build_test_zpl(printer, args.ticket_id, args.qty)
    else:
        with open(args.fields_config) as f:
            fields_data = yaml.safe_load(f) or {}
        fields_config = fields_data.get("fields") or {}
        ticket_url_pattern = fields_data.get("ticket_url_pattern", "")
        created_date = args.created_date or datetime.now(timezone.utc).isoformat()
        ticket_json = {
            "id": args.ticket_id,
            "dateoccurred": created_date,
            "client_name": "Acme Corp",
            "site_name": "Main Office",
            "user_name": "Jane Smith",
        }
        zpl = render_label(
            ticket_json,
            printer,
            args.qty,
            fields_config,
            ticket_url_pattern,
        )

    print(f"Sending {args.qty} label(s) to {printer.name} ({printer.ip}:{printer.port})...")
    send(printer, zpl)
    print("Sent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
