"""Raw ZPL send over TCP 9100 (Zebra/JetDirect AppSocket printing port).

A successful send() means the printer *accepted the bytes* over the socket,
not that a label physically printed (out of media, head open, and paused
all still accept jobs). Callers should phrase success messages as "Sent",
not "Printed". # TODO (v1.1): query printer status with ~HS before sending
and surface paper-out/pause in the failure note.
"""

import logging
import socket
import time
from pathlib import Path

import yaml

from .models import Printer

logger = logging.getLogger(__name__)


def load_printers(config_path: str | Path) -> dict[str, Printer]:
    with open(config_path, "r") as f:
        data = yaml.safe_load(f) or {}
    printers: dict[str, Printer] = {}
    for printer_id, cfg in (data.get("printers") or {}).items():
        printers[printer_id] = Printer(
            id=printer_id,
            name=cfg["name"],
            ip=cfg["ip"],
            port=cfg.get("port", 9100),
            dpi=cfg.get("dpi", 203),
            label_width_in=cfg["label_width_in"],
            label_height_in=cfg["label_height_in"],
            connect_timeout_seconds=cfg.get("connect_timeout_seconds", 5),
            retries=cfg.get("retries", 3),
            qr_magnification=cfg.get("qr_magnification", 10),
        )
    return printers


def send(printer: Printer, zpl: bytes) -> None:
    """Send raw ZPL bytes to printer.ip:printer.port. Retries with backoff
    on connection failure (printer.retries attempts total), then raises
    ConnectionError so the caller can record a failure note."""
    attempts = max(1, printer.retries)
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            with socket.create_connection(
                (printer.ip, printer.port), timeout=printer.connect_timeout_seconds
            ) as sock:
                sock.sendall(zpl)
            return
        except OSError as exc:
            last_exc = exc
            logger.warning(
                "printer send attempt %d/%d to %s (%s:%d) failed: %s",
                attempt,
                attempts,
                printer.name,
                printer.ip,
                printer.port,
                exc,
            )
            if attempt < attempts:
                time.sleep(min(2 ** (attempt - 1), 10))
    raise ConnectionError(
        f"failed to send ZPL to {printer.name} ({printer.ip}:{printer.port}) "
        f"after {attempts} attempt(s)"
    ) from last_exc
