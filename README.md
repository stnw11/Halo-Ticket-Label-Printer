# Halo-Ticket-Label-Printer

Polls HaloITSM for tickets where an agent has set a quantity field, claims
each ticket, and prints that many 2" x 4" labels — ticket id, a scannable
QR code linking back to the ticket, organization/site/end user, and the
ticket's created date — to a Zebra ZD411 (or compatible ZPL printer) on
the LAN.

## Architecture

Every network connection this service makes is **outbound**: it polls
Halo's API over HTTPS, and it sends raw ZPL to the printer over a
LAN-local TCP socket (port 9100). Nothing listens for inbound traffic —
no port-forward, no VPN, no firewall exception needed on the printer's
network.

```
HaloITSM (cloud)  <--HTTPS poll/claim--  this service  --raw ZPL, TCP 9100-->  Zebra printer (LAN)
```

The tradeoff of polling is latency (labels print within one poll interval,
not instantly) and API call volume (bounded and configurable). For a
"press a button, walk to the printer" workflow, a 10–20 second poll
interval is imperceptible in practice.

## Label design

- **Ticket id** — large, bold, right-justified against the QR code's far
  edge (so it lines up regardless of how many digits the ticket number
  has).
- **QR code** — links to the ticket in a browser (`config/fields.yaml`'s
  `ticket_url_pattern`), using ZPL's native `^BQ` command (no image
  library needed).
- **Organization, site, end user, created date** — stacked on the
  opposite 2" edge from the ticket id/QR code. A fifth row is reserved
  but left blank by default for a future field (`site_address` — see the
  comment in `config/fields.yaml` to enable it).

Field mappings live in `config/fields.yaml` (Halo ticket JSON path ->
label variable), so adding/removing/remapping fields is a config edit,
not a code change. Exact `^FO` positions, font sizes, and the QR's
magnification are computed in `src/label_renderer.py` from
`config/printers.yaml`'s per-printer settings, scaled by `dpi / 203` so
the same template works on a 300 dpi printer too — see that file's
comments for the dot-math, and "Known ZPL quirks" below for two
non-obvious rotation behaviors worth knowing before changing it.

This layout has been validated end-to-end on real Zebra ZD411 hardware:
correct orientation, all fields positioned without clipping or overlap,
correct physical dimensions (2" x 4"), and a QR code that scans cleanly to
the ticket.

## HaloITSM one-time setup

1. **Custom field.** Configuration > Custom Fields > new Integer field
   (e.g. `CFLabelPrintQty`), default `0`/blank — verify this after
   creating it, since a positive default would make every new ticket
   queue a print job the moment the field appears on screen. Put it on
   the ticket screen/tab where agents will see it; typing a number and
   saving is the entire trigger. Note the field's numeric Halo id (used
   by `HALO_LABEL_QTY_FIELD_ID`) and its exact name/casing (used by
   `HALO_LABEL_QTY_FIELD_NAME`) — Halo custom field names are
   case-sensitive, and a mismatch fails silently (`get_pending()` just
   never finds anything pending, no error).
2. **Button (optional).** Not required — the field itself is the trigger.
   If your Toolbar Designer supports it, a button that sets the field to
   `1` is a nice one-click shortcut for the common case.
3. **Saved view.** Tickets > Views/Lists, filtered on the qty field > 0.
   Depending on your tenant's Halo version, `GET /Tickets` may have no
   query parameter that filters by a custom field's value at all — check
   your tenant's Swagger doc (see below) before assuming you can skip
   this. If there's truly no such parameter, the saved view is the only
   way to filter pending tickets server-side; note its id for
   `HALO_VIEW_ID`.
4. **API application.** Configuration > Integrations > HaloITSM API > View
   Applications > New, using Client ID/Secret (Services) auth. Use an
   "Application identity" login if your version offers one; otherwise a
   dedicated API-only agent works identically for OAuth2
   client-credentials purposes. Scope permissions to only: read tickets
   (list + get) and update the qty custom field.
   - **Two permission layers, not one.** Granting the API application's
     own OAuth scopes is necessary but not always sufficient — some Halo
     versions separately enforce the underlying agent record's own
     permissions. If `/api/Tickets` calls return 403/401 despite a token
     whose granted `scope` includes `read:tickets`/`edit:tickets`, check
     that agent's own ticket-area permissions too.
   - Record the **Client ID**, **Client Secret**, and **token endpoint
     URL** exactly as shown on the API application screen — copy/paste
     rather than retyping, since a single dropped character (a hyphen, a
     wrong slash direction) can produce a token request that fails in a
     way that's easy to misdiagnose.
5. **Check your tenant's Swagger** (Configuration > Integrations >
   HaloITSM API > API application > API documentation link) before
   wiring anything up — Halo's REST API has evolved across versions, so
   confirm against your own instance:
   - The HTTP verb and payload shape for `POST`/`PATCH /Tickets` custom
     field updates (commonly an array-wrapped ticket object with
     `customfields: [{"id": <field_id>, "value": ...}]`).
   - Whether `GET /Tickets` supports `view_id` and an
     `includedetails`-style parameter (e.g. `include_custom_fields`) for
     getting custom field values back in the list response.
   - The token response's `scope` field, if your token is opaque (not a
     decodable JWT) — useful for debugging permission issues without
     needing to inspect the token itself.

## Configuration

All hot-editable — no rebuild required for changes.

- **`.env`** (secrets + core settings, never committed — copy
  `.env.example` to start): Halo connection details, `POLL_INTERVAL_SECONDS`,
  `MAX_LABELS_PER_JOB` (a fat-fingered quantity is clamped, not fatal),
  `LOG_LEVEL`.
- **`config/printers.yaml`** — printer IP/port/dpi/label size per printer
  id, plus `qr_magnification` (tune against a real printout).
- **`config/routing.yaml`** — maps a ticket field value to a printer id,
  so multi-printer routing is a config edit, not a code change; a single
  printer needs nothing beyond `default_printer`.
- **`config/fields.yaml`** — Halo ticket JSON path -> label template
  variable, plus the QR code's `ticket_url_pattern`.
- **`config/labels/default.zpl.j2`** — the Jinja2 ZPL template itself.

## Local development

Everything can be developed and tested bare (no Docker):

```
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m src.main
```

`tools/print_test_label.py` sends a sample label straight to a printer, no
Halo needed — useful for validating printer connectivity and label layout
in isolation:

```
python -m tools.print_test_label --printer default --ticket-id TEST-1 --qty 1
```

Tests: `pytest`.

## Docker deployment

```
cp .env.example .env   # fill in your values
docker compose up -d --build
docker compose ps      # should show "healthy" within ~45s
docker compose logs -f
```

- `config/` is bind-mounted, so editing a YAML file doesn't require a
  rebuild — only changes to `src/`, `requirements.txt`, or the
  `Dockerfile` itself do.
- The healthcheck watches `/tmp/heartbeat`'s mtime, touched at the top of
  every poll iteration; it goes unhealthy if that goes stale for more
  than 3 poll intervals (45s at the default `POLL_INTERVAL_SECONDS=15` —
  keep this in sync if you change the interval).
- Runs as a non-root user, no ports published — every connection this
  service makes is outbound.
- `restart: unless-stopped` — combined with the catch-up-after-downtime
  behavior below, a host reboot is expected to self-heal without manual
  intervention. In testing, killing the main process directly (`docker
  kill --signal=SIGKILL <container>`) correctly flipped the healthcheck to
  `unhealthy` before the container exited, but the automatic restart
  didn't reliably fire within Docker Desktop for macOS in that exact
  scenario — `docker compose up -d` brought it back immediately when run
  manually. If you rely on this for unattended recovery, verify the
  restart behavior against your actual deployment target (a Linux Docker
  host may behave differently than Docker Desktop's VM).

## Known ZPL quirks

Two non-obvious ZPL rotation behaviors, already handled correctly in
`src/label_renderer.py` but worth knowing before changing the template or
the dot-math:

- **Both `^BQ` (QR code) and rotated `^A0R` text fields grow in the +x/+y
  direction from their `^FO` anchor point** — i.e. the anchor is always
  the *near* edge of the element, never the far one. Getting this
  backwards produces either a corrupted/clipped QR code (it runs off the
  printable canvas) or overlapping/clipped text, depending on which
  element it's applied to.
- **`^BQ`'s physical size isn't reliably predictable from the
  magnification parameter alone** — it also depends on the encoded data's
  length (which determines the QR version/module count). Calibrate
  empirically against a real printout rather than assuming a fixed
  dots-per-magnification ratio (see
  `_QR_DOTS_PER_MAGNIFICATION_UNIT_AT_203DPI` in `label_renderer.py`); if
  your `ticket_url_pattern` is significantly longer or shorter than the
  default, re-verify.

## Known limitations (by design)

- **Claim-before-print ordering.** The ticket's quantity field is reset to
  0 *before* printing, not after. If the service crashes mid-print, the
  job is silently lost — no label, no error note. This is a deliberate
  tradeoff: clearing the field *after* printing instead would risk
  physically duplicate labels if a crash/restart happened between
  printing and clearing, which is worse than a silently lost job.
  Recovery is manual: the agent notices no label appeared and re-enters
  the quantity.
- **No on-ticket failure signal.** Outcome (success or failure) is only
  visible in this service's own logs, not on the ticket itself. A failed
  print (printer unplugged, out of media) leaves no trace an agent would
  see without checking the logs.
- **Single instance only.** The claim step is safe only because there is
  exactly one writer. Do not run multiple replicas without adding real
  distributed locking.
- **Catch-up after downtime is intentional.** If the service is down (host
  reboot, outage) while agents queue prints, everything pending prints
  when it comes back up. This is desired, not a bug — but expect a burst
  of labels after a restart.
- **"Sent" vs "printed."** A successful send means the printer accepted
  the bytes over the socket, not that a label physically came out
  (paper-out, head-open, and paused all still accept jobs).

## License

MIT — see [LICENSE](LICENSE).
