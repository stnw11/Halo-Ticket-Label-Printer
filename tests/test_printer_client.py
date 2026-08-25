from unittest.mock import MagicMock, patch

import pytest

from src.models import Printer
from src.printer_client import send


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


def test_send_success():
    printer = make_printer()
    mock_sock = MagicMock()
    mock_sock.__enter__.return_value = mock_sock
    with patch("src.printer_client.socket.create_connection", return_value=mock_sock) as mock_conn:
        send(printer, b"^XA^XZ")
    mock_conn.assert_called_once_with(("10.0.0.5", 9100), timeout=5)
    mock_sock.sendall.assert_called_once_with(b"^XA^XZ")


def test_send_retries_then_raises(monkeypatch):
    printer = make_printer(retries=3)
    monkeypatch.setattr("src.printer_client.time.sleep", lambda *_: None)
    with patch("src.printer_client.socket.create_connection", side_effect=OSError("refused")) as mock_conn:
        with pytest.raises(ConnectionError):
            send(printer, b"^XA^XZ")
    assert mock_conn.call_count == 3


def test_send_retries_then_succeeds(monkeypatch):
    printer = make_printer(retries=3)
    monkeypatch.setattr("src.printer_client.time.sleep", lambda *_: None)
    mock_sock = MagicMock()
    mock_sock.__enter__.return_value = mock_sock
    with patch(
        "src.printer_client.socket.create_connection",
        side_effect=[OSError("refused"), mock_sock],
    ) as mock_conn:
        send(printer, b"^XA^XZ")
    assert mock_conn.call_count == 2
    mock_sock.sendall.assert_called_once_with(b"^XA^XZ")


def test_load_printers(tmp_path):
    config = tmp_path / "printers.yaml"
    config.write_text(
        """
printers:
  default:
    name: "Front Desk ZD411"
    ip: "10.10.5.50"
    port: 9100
    dpi: 203
    label_width_in: 2.0
    label_height_in: 4.0
    connect_timeout_seconds: 5
    retries: 3
"""
    )
    from src.printer_client import load_printers

    printers = load_printers(config)
    assert set(printers) == {"default"}
    assert printers["default"].ip == "10.10.5.50"
    assert printers["default"].dpi == 203
