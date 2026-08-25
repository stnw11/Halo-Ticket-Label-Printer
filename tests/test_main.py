from unittest.mock import MagicMock, patch

import pytest
import requests

from src.main import StartupError, clamp_qty, compute_backoff, load_config, poll_once
from src.models import Printer, Ticket

REQUIRED_ENV = {
    "HALO_BASE_URL": "https://example.haloitsm.com",
    "HALO_AUTH_URL": "https://example.haloitsm.com/auth/token",
    "HALO_CLIENT_ID": "client-id",
    "HALO_CLIENT_SECRET": "client-secret",
    "HALO_VIEW_ID": "56",
    "HALO_LABEL_QTY_FIELD_ID": "300",
}


def _set_required_env(monkeypatch):
    for key, value in REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)


def _write_valid_config(config_dir):
    (config_dir / "printers.yaml").write_text(
        """
printers:
  default:
    name: "Test Printer"
    ip: "10.0.0.5"
    port: 9100
    dpi: 203
    label_width_in: 2.0
    label_height_in: 4.0
    connect_timeout_seconds: 5
    retries: 3
"""
    )
    (config_dir / "fields.yaml").write_text(
        """
fields:
  ticket_id: "id"
  summary: "summary"
ticket_url_pattern: "https://example.com/tickets/{ticket_id}"
"""
    )
    (config_dir / "routing.yaml").write_text(
        """
match_field: "team"
rules: {}
default_printer: "default"
"""
    )


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
        qr_magnification=10,
    )
    defaults.update(overrides)
    return Printer(**defaults)


def make_config():
    return {
        "printers": {"default": make_printer()},
        "fields_config": {"ticket_id": "id", "summary": "summary"},
        "ticket_url_pattern": "https://example.com/tickets/{ticket_id}",
        "routing_config": {"match_field": "team", "rules": {}, "default_printer": "default"},
    }


def make_ticket(ticket_id="1", qty=2):
    return Ticket(id=ticket_id, label_print_qty=qty, raw={"id": ticket_id, "summary": "test", "team": "x"})


# --- clamp_qty -------------------------------------------------------------


def test_clamp_qty_within_range_unchanged():
    assert clamp_qty(5, 50) == 5


def test_clamp_qty_caps_at_max():
    assert clamp_qty(500, 50) == 50


def test_clamp_qty_floors_at_one():
    assert clamp_qty(0, 50) == 1
    assert clamp_qty(-3, 50) == 1


# --- poll_once: claim-failure-skips-printing --------------------------------


def test_claim_failure_skips_printing_and_continues_batch():
    halo_client = MagicMock()
    halo_client.get_pending.return_value = [make_ticket("1"), make_ticket("2")]
    halo_client.claim.side_effect = [Exception("claim failed"), None]

    with patch("src.main.send") as mock_send:
        poll_once(halo_client, make_config(), max_labels_per_job=50)

    # ticket 1's claim failed -> never printed; ticket 2 succeeded -> printed
    assert mock_send.call_count == 1


def test_all_claims_failing_does_not_raise():
    halo_client = MagicMock()
    halo_client.get_pending.return_value = [make_ticket("1")]
    halo_client.claim.side_effect = Exception("halo down for writes")

    with patch("src.main.send") as mock_send:
        poll_once(halo_client, make_config(), max_labels_per_job=50)  # must not raise

    mock_send.assert_not_called()


# --- poll_once: one bad ticket never stops the batch ------------------------


def test_print_failure_for_one_ticket_does_not_stop_others():
    halo_client = MagicMock()
    halo_client.get_pending.return_value = [make_ticket("1"), make_ticket("2")]
    halo_client.claim.return_value = None

    with patch(
        "src.main.send", side_effect=[ConnectionError("printer unplugged"), None]
    ) as mock_send:
        poll_once(halo_client, make_config(), max_labels_per_job=50)  # must not raise

    assert mock_send.call_count == 2  # both attempted despite the first failing


def test_successful_ticket_claims_then_prints_with_clamped_qty():
    halo_client = MagicMock()
    halo_client.get_pending.return_value = [make_ticket("1", qty=500)]

    with patch("src.main.send") as mock_send:
        poll_once(halo_client, make_config(), max_labels_per_job=50)

    halo_client.claim.assert_called_once_with("1")
    mock_send.assert_called_once()
    printer_arg, zpl_arg = mock_send.call_args[0]
    assert b"^PQ50" in zpl_arg  # clamped from 500 to 50


def test_get_pending_failure_propagates_for_caller_to_handle():
    halo_client = MagicMock()
    halo_client.get_pending.side_effect = ConnectionError("Halo unreachable")

    with pytest.raises(ConnectionError):
        poll_once(halo_client, make_config(), max_labels_per_job=50)


# --- compute_backoff ---------------------------------------------------------


def test_compute_backoff_doubles_on_generic_error():
    sleep_seconds, next_backoff = compute_backoff(10, Exception("boom"), poll_interval=15, max_backoff=300)
    assert sleep_seconds == 10
    assert next_backoff == 20


def test_compute_backoff_caps_at_max():
    sleep_seconds, next_backoff = compute_backoff(250, Exception("boom"), poll_interval=15, max_backoff=300)
    assert next_backoff == 300


def test_compute_backoff_honors_retry_after_on_429():
    response = MagicMock()
    response.status_code = 429
    response.headers = {"Retry-After": "42"}
    exc = requests.HTTPError("429")
    exc.response = response

    sleep_seconds, next_backoff = compute_backoff(10, exc, poll_interval=15, max_backoff=300)
    assert sleep_seconds == 42.0
    assert next_backoff == 15  # resets to poll_interval rather than continuing to grow



# --- load_config: startup fail-fast (non-negotiable per §4.3) --------------


def test_load_config_fails_on_missing_env_vars(monkeypatch, tmp_path):
    monkeypatch.delenv("HALO_BASE_URL", raising=False)
    _write_valid_config(tmp_path)
    with pytest.raises(StartupError, match="HALO_BASE_URL"):
        load_config(config_dir=tmp_path)


def test_load_config_fails_on_malformed_yaml(monkeypatch, tmp_path):
    _set_required_env(monkeypatch)
    _write_valid_config(tmp_path)
    (tmp_path / "printers.yaml").write_text("printers: [this is not: valid: yaml")
    with pytest.raises(StartupError, match="printers.yaml"):
        load_config(config_dir=tmp_path)


def test_load_config_fails_when_no_printers_defined(monkeypatch, tmp_path):
    _set_required_env(monkeypatch)
    _write_valid_config(tmp_path)
    (tmp_path / "printers.yaml").write_text("printers: {}\n")
    with pytest.raises(StartupError, match="no printers"):
        load_config(config_dir=tmp_path)


def test_load_config_fails_when_default_printer_unknown(monkeypatch, tmp_path):
    _set_required_env(monkeypatch)
    _write_valid_config(tmp_path)
    (tmp_path / "routing.yaml").write_text('match_field: "team"\nrules: {}\ndefault_printer: "nope"\n')
    with pytest.raises(StartupError, match="default_printer"):
        load_config(config_dir=tmp_path)


def test_load_config_succeeds_with_valid_config(monkeypatch, tmp_path):
    _set_required_env(monkeypatch)
    _write_valid_config(tmp_path)
    config = load_config(config_dir=tmp_path)
    assert "default" in config["printers"]
    assert config["routing_config"]["default_printer"] == "default"


def test_compute_backoff_ignores_malformed_retry_after():
    response = MagicMock()
    response.status_code = 429
    response.headers = {"Retry-After": "not-a-number"}
    exc = requests.HTTPError("429")
    exc.response = response

    sleep_seconds, next_backoff = compute_backoff(10, exc, poll_interval=15, max_backoff=300)
    assert sleep_seconds == 10
    assert next_backoff == 20
