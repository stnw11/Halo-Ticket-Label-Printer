"""OAuth2 client-credentials token management and ticket read/claim/note
operations against HaloITSM."""

import logging
import time

import requests

from .fields import resolve_path
from .models import Ticket

logger = logging.getLogger(__name__)

TOKEN_EXPIRY_MARGIN_SECONDS = 60
DEFAULT_USER_AGENT = "Halo-Ticket-Label-Printer/1.0 (+https://github.com/stnw11/Halo-Ticket-Label-Printer)"


class HaloClient:
    def __init__(
        self,
        base_url: str,
        auth_url: str,
        client_id: str,
        client_secret: str,
        view_id: str,
        qty_field_id: str,
        qty_field_name: str,
        user_agent: str,
        note_outcome_id: str | None = None,
        timeout: int = 10,
    ):
        self.base_url = base_url.rstrip("/")
        self.auth_url = auth_url
        self.client_id = client_id
        self.client_secret = client_secret
        self.view_id = view_id
        self.qty_field_id = qty_field_id
        self.qty_field_name = f"cf:{qty_field_name}"
        self.user_agent = user_agent
        self.note_outcome_id = note_outcome_id
        self.timeout = timeout
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    def _headers(self, authorized: bool = True) -> dict:
        headers = {"User-Agent": self.user_agent}
        if authorized:
            headers["Authorization"] = f"Bearer {self.get_token()}"
        return headers

    def get_token(self) -> str:
        """Fetch and cache an OAuth2 client-credentials token, refreshing
        shortly before it expires. Sends the required User-Agent header
        on the token request itself, not just subsequent API calls."""
        if self._token and time.time() < self._token_expires_at:
            return self._token

        logger.debug("fetching Halo token from %s", self.auth_url)
        response = requests.post(
            self.auth_url,
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "scope": "all",
            },
            headers={"User-Agent": self.user_agent},
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        self._token = payload["access_token"]
        expires_in = payload.get("expires_in", 3600)
        self._token_expires_at = time.time() + expires_in - TOKEN_EXPIRY_MARGIN_SECONDS
        return self._token

    def get_pending(self) -> list[Ticket]:
        """GET /Tickets filtered by the saved view (qty field > 0), with
        custom field values included. Re-checks qty > 0 client-side as a
        defensive backstop in case the view definition ever drifts."""
        response = requests.get(
            f"{self.base_url}/api/Tickets",
            params={"view_id": self.view_id, "include_custom_fields": self.qty_field_id},
            headers=self._headers(),
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        raw_tickets = payload.get("tickets", []) if isinstance(payload, dict) else payload

        tickets = []
        for raw in raw_tickets:
            qty_str = resolve_path(raw, self.qty_field_name)
            try:
                qty = int(qty_str) if qty_str else 0
            except ValueError:
                qty = 0
            if qty > 0:
                tickets.append(Ticket(id=str(raw.get("id")), label_print_qty=qty, raw=raw))
        return tickets

    def claim(self, ticket_id: str) -> None:
        """Write the qty field back to 0. Called immediately after reading
        a ticket off the pending list, before printing -- this is the
        entire "claim" operation."""
        response = requests.post(
            f"{self.base_url}/api/Tickets",
            json=[{"id": ticket_id, "customfields": [{"id": self.qty_field_id, "value": "0"}]}],
            headers=self._headers(),
            timeout=self.timeout,
        )
        response.raise_for_status()

    def add_note(self, ticket_id: str, text: str) -> None:
        """Post an internal, agent-visible (not customer-visible) note to
        the ticket recording the print outcome. Callers should treat this
        as best-effort -- a note failure should never be conflated with a
        print failure."""
        action: dict = {"ticket_id": ticket_id, "note": text, "hiddenfromuser": True}
        if self.note_outcome_id:
            action["outcome"] = self.note_outcome_id
        response = requests.post(
            f"{self.base_url}/api/Actions",
            json=[action],
            headers=self._headers(),
            timeout=self.timeout,
        )
        response.raise_for_status()
