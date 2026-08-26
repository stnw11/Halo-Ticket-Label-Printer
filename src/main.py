"""Entrypoint: poll loop + heartbeat.

Error-handling rules this module must follow:
- A failed claim means skip the ticket entirely -- it stays qty > 0 in
  Halo and is retried next poll. Printing before a confirmed claim is
  forbidden (the one way this design could produce duplicate labels).
- Everything per-ticket is wrapped so one failure can't prevent the rest
  of the batch from printing; everything per-poll is wrapped so no
  Halo/API failure can kill the loop.
- The container only ever exits on startup misconfiguration (bad YAML,
  missing env vars) -- fail fast there, with a clear message.
- Posting the audit note is best-effort: a note failure is logged but
  never treated as a print failure, and never blocks/retries the ticket.
"""

import logging
import os
import time
from pathlib import Path

import yaml

from .env import load_dotenv
from .fields import load_fields_config
from .halo_client import DEFAULT_USER_AGENT, HaloClient
from .label_renderer import render as render_label
from .printer_client import load_printers, send
from .router import load_routing_config, resolve_printer_id

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
HEARTBEAT_PATH = Path("/tmp/heartbeat")

REQUIRED_ENV_VARS = [
    "HALO_BASE_URL",
    "HALO_AUTH_URL",
    "HALO_CLIENT_ID",
    "HALO_CLIENT_SECRET",
    "HALO_VIEW_ID",
    "HALO_LABEL_QTY_FIELD_ID",
]
MAX_BACKOFF_SECONDS = 300


class StartupError(Exception):
    """Raised for bad config/env at startup -- the only case main() exits."""


def clamp_qty(requested: int, max_qty: int) -> int:
    return max(1, min(requested, max_qty))


def load_config(config_dir: Path = REPO_ROOT / "config") -> dict:
    missing = [name for name in REQUIRED_ENV_VARS if not os.environ.get(name)]
    if missing:
        raise StartupError(f"missing required environment variable(s): {', '.join(missing)}")

    try:
        printers = load_printers(config_dir / "printers.yaml")
    except (OSError, yaml.YAMLError) as e:
        raise StartupError(f"failed to load config/printers.yaml: {e}") from e

    try:
        fields_config = load_fields_config(config_dir / "fields.yaml")
        with open(config_dir / "fields.yaml") as f:
            fields_data = yaml.safe_load(f) or {}
        ticket_url_pattern = fields_data.get("ticket_url_pattern", "")
    except (OSError, yaml.YAMLError) as e:
        raise StartupError(f"failed to load config/fields.yaml: {e}") from e

    try:
        routing_config = load_routing_config(config_dir / "routing.yaml")
    except (OSError, yaml.YAMLError) as e:
        raise StartupError(f"failed to load config/routing.yaml: {e}") from e

    if not printers:
        raise StartupError("config/printers.yaml defines no printers")
    default_printer_id = routing_config.get("default_printer")
    if default_printer_id not in printers:
        raise StartupError(
            f"routing.yaml's default_printer '{default_printer_id}' is not in printers.yaml"
        )

    return {
        "printers": printers,
        "fields_config": fields_config,
        "ticket_url_pattern": ticket_url_pattern,
        "routing_config": routing_config,
    }


def build_halo_client() -> HaloClient:
    return HaloClient(
        base_url=os.environ["HALO_BASE_URL"],
        auth_url=os.environ["HALO_AUTH_URL"],
        client_id=os.environ["HALO_CLIENT_ID"],
        client_secret=os.environ["HALO_CLIENT_SECRET"],
        view_id=os.environ["HALO_VIEW_ID"],
        qty_field_id=os.environ["HALO_LABEL_QTY_FIELD_ID"],
        qty_field_name=os.environ.get("HALO_LABEL_QTY_FIELD_NAME", "CFLabelPrintQty"),
        user_agent=DEFAULT_USER_AGENT,
        note_outcome_id=os.environ.get("HALO_NOTE_OUTCOME_ID") or None,
    )


def _add_note_best_effort(halo_client: HaloClient, ticket_id: str, text: str) -> None:
    """Never let a note failure look like a print failure -- log and move
    on."""
    try:
        halo_client.add_note(ticket_id, text)
    except Exception:
        logger.warning("ticket %s: failed to post audit note", ticket_id, exc_info=True)


def touch_heartbeat() -> None:
    HEARTBEAT_PATH.touch()


def poll_once(halo_client: HaloClient, config: dict, max_labels_per_job: int) -> None:
    """One pass: fetch pending tickets, claim+print each. Raises only if
    get_pending() itself fails (Halo down) -- the caller decides how to
    back off. Per-ticket failures are caught here and never propagate."""
    tickets = halo_client.get_pending()

    for ticket in tickets:
        try:
            qty = clamp_qty(ticket.label_print_qty, max_labels_per_job)
            halo_client.claim(ticket.id)
        except Exception:
            logger.warning(
                "ticket %s: claim failed, skipping -- retried next poll", ticket.id, exc_info=True
            )
            continue  # NEVER print an unclaimed ticket

        try:
            printer_id = resolve_printer_id(ticket.raw, config["routing_config"])
            printer = config["printers"][printer_id]
            zpl = render_label(
                ticket.raw,
                printer,
                qty,
                config["fields_config"],
                config["ticket_url_pattern"],
            )
            send(printer, zpl)
            note_text = f"{qty} label(s) sent to {printer.name}"
            if qty != ticket.label_print_qty:
                note_text += f" (requested {ticket.label_print_qty}, capped at {max_labels_per_job})"
                logger.info(
                    "ticket %s: requested %d label(s), capped at %d",
                    ticket.id,
                    ticket.label_print_qty,
                    max_labels_per_job,
                )
            logger.info("ticket %s: sent %d label(s) to %s", ticket.id, qty, printer.name)
            _add_note_best_effort(halo_client, ticket.id, note_text)
        except Exception as e:
            logger.error("ticket %s: label print failed", ticket.id, exc_info=True)
            _add_note_best_effort(halo_client, ticket.id, f"Label print failed: {e}")
        # one bad ticket never stops the rest of the batch


def compute_backoff(
    current_backoff: float, exc: Exception, poll_interval: float, max_backoff: float = MAX_BACKOFF_SECONDS
) -> tuple[float, float]:
    """Decide how long to sleep after a failed poll (honoring Retry-After
    on 429) and the next backoff value to use if it fails again. Returns
    (sleep_seconds, next_backoff)."""
    response = getattr(exc, "response", None)
    if response is not None and getattr(response, "status_code", None) == 429:
        retry_after = getattr(response, "headers", {}).get("Retry-After")
        if retry_after:
            try:
                return float(retry_after), poll_interval
            except ValueError:
                pass
    return current_backoff, min(current_backoff * 2, max_backoff)


def run(halo_client: HaloClient, config: dict, max_labels_per_job: int, poll_interval: float) -> None:
    """The infinite poll loop. Kept thin -- poll_once() and compute_backoff()
    hold the actual logic and are unit tested directly."""
    backoff = poll_interval
    while True:
        touch_heartbeat()
        try:
            poll_once(halo_client, config, max_labels_per_job)
            backoff = poll_interval
            time.sleep(poll_interval)
        except Exception as e:
            sleep_seconds, backoff = compute_backoff(backoff, e, poll_interval)
            logger.warning("poll failed: %s -- retrying in %.0fs", e, sleep_seconds, exc_info=True)
            time.sleep(sleep_seconds)


def main() -> None:
    load_dotenv()
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        config = load_config()
    except StartupError as e:
        logger.error("startup misconfiguration: %s", e)
        raise SystemExit(1) from e

    halo_client = build_halo_client()
    max_labels_per_job = int(os.environ.get("MAX_LABELS_PER_JOB", 50))
    poll_interval = int(os.environ.get("POLL_INTERVAL_SECONDS", 15))

    run(halo_client, config, max_labels_per_job, poll_interval)


if __name__ == "__main__":
    main()
