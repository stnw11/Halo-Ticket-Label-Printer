import time
from unittest.mock import MagicMock, patch

import pytest
import requests

from src.halo_client import HaloClient

USER_AGENT = "Halo-Ticket-Label-Printer/1.0 (+https://github.com/example/Halo-Ticket-Label-Printer)"


def make_client(**overrides):
    defaults = dict(
        base_url="https://yourtenant.haloitsm.com",
        auth_url="https://yourtenant.haloitsm.com/auth/token",
        client_id="client-id",
        client_secret="client-secret",
        view_id="56",
        qty_field_id="300",
        qty_field_name="CFLabelPrintQty",
        user_agent=USER_AGENT,
        timeout=5,
    )
    defaults.update(overrides)
    return HaloClient(**defaults)


def mock_response(json_data, status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    if status_code >= 400:
        resp.raise_for_status.side_effect = requests.HTTPError(f"{status_code} error")
    else:
        resp.raise_for_status.return_value = None
    return resp


# --- get_token -------------------------------------------------------------


def test_get_token_fetches_and_caches():
    client = make_client()
    token_response = mock_response({"access_token": "abc123", "expires_in": 3600})
    with patch("src.halo_client.requests.post", return_value=token_response) as mock_post:
        token1 = client.get_token()
        token2 = client.get_token()
    assert token1 == "abc123"
    assert token2 == "abc123"
    mock_post.assert_called_once()  # second call served from cache


def test_get_token_sends_user_agent_and_credentials():
    client = make_client()
    token_response = mock_response({"access_token": "abc123", "expires_in": 3600})
    with patch("src.halo_client.requests.post", return_value=token_response) as mock_post:
        client.get_token()
    _, kwargs = mock_post.call_args
    assert kwargs["headers"]["User-Agent"] == USER_AGENT
    assert kwargs["data"]["grant_type"] == "client_credentials"
    assert kwargs["data"]["client_id"] == "client-id"
    assert kwargs["data"]["client_secret"] == "client-secret"


def test_get_token_refetches_after_expiry():
    client = make_client()
    token_response = mock_response({"access_token": "abc123", "expires_in": 100})
    with patch("src.halo_client.requests.post", return_value=token_response) as mock_post:
        client.get_token()
        client._token_expires_at = time.time() - 1  # force expiry
        client.get_token()
    assert mock_post.call_count == 2


def test_get_token_raises_on_http_error():
    client = make_client()
    error_response = mock_response({}, status_code=401)
    with patch("src.halo_client.requests.post", return_value=error_response):
        with pytest.raises(requests.HTTPError):
            client.get_token()


# --- get_pending -------------------------------------------------------------


def _ticket_json(ticket_id, qty):
    return {
        "id": ticket_id,
        "summary": "test ticket",
        "customfields": [{"id": 300, "name": "CFLabelPrintQty", "value": qty}],
    }


def test_get_pending_sends_view_id_and_include_custom_fields():
    client = make_client()
    token_response = mock_response({"access_token": "abc123", "expires_in": 3600})
    list_response = mock_response({"tickets": [_ticket_json(1, 2)]})
    with patch("src.halo_client.requests.post", return_value=token_response):
        with patch("src.halo_client.requests.get", return_value=list_response) as mock_get:
            client.get_pending()
    _, kwargs = mock_get.call_args
    assert kwargs["params"] == {"view_id": "56", "include_custom_fields": "300"}
    assert kwargs["headers"]["Authorization"] == "Bearer abc123"


def test_get_pending_parses_wrapped_response_and_filters_qty():
    client = make_client()
    token_response = mock_response({"access_token": "abc123", "expires_in": 3600})
    list_response = mock_response(
        {"tickets": [_ticket_json(1, 2), _ticket_json(2, 0), _ticket_json(3, 5)]}
    )
    with patch("src.halo_client.requests.post", return_value=token_response):
        with patch("src.halo_client.requests.get", return_value=list_response):
            tickets = client.get_pending()
    assert {t.id for t in tickets} == {"1", "3"}
    assert {t.label_print_qty for t in tickets} == {2, 5}


def test_get_pending_parses_bare_list_response():
    client = make_client()
    token_response = mock_response({"access_token": "abc123", "expires_in": 3600})
    list_response = mock_response([_ticket_json(7, 3)])
    with patch("src.halo_client.requests.post", return_value=token_response):
        with patch("src.halo_client.requests.get", return_value=list_response):
            tickets = client.get_pending()
    assert len(tickets) == 1
    assert tickets[0].id == "7"
    assert tickets[0].label_print_qty == 3


def test_get_pending_raises_on_http_error():
    client = make_client()
    token_response = mock_response({"access_token": "abc123", "expires_in": 3600})
    error_response = mock_response({}, status_code=500)
    with patch("src.halo_client.requests.post", return_value=token_response):
        with patch("src.halo_client.requests.get", return_value=error_response):
            with pytest.raises(requests.HTTPError):
                client.get_pending()


# --- claim -------------------------------------------------------------


def test_claim_posts_expected_payload_shape():
    client = make_client()
    token_response = mock_response({"access_token": "abc123", "expires_in": 3600})
    claim_response = mock_response([{"id": 42}])
    with patch("src.halo_client.requests.post") as mock_post:
        mock_post.side_effect = [token_response, claim_response]
        client.claim("42")
    _, kwargs = mock_post.call_args
    assert kwargs["json"] == [{"id": "42", "customfields": [{"id": "300", "value": "0"}]}]
    assert kwargs["headers"]["Authorization"] == "Bearer abc123"


def test_claim_raises_on_http_error():
    client = make_client()
    token_response = mock_response({"access_token": "abc123", "expires_in": 3600})
    error_response = mock_response({}, status_code=400)
    with patch("src.halo_client.requests.post") as mock_post:
        mock_post.side_effect = [token_response, error_response]
        with pytest.raises(requests.HTTPError):
            client.claim("42")
