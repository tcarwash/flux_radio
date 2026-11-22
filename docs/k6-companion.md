# Flux K6 Companion

A small, self-hosted browser front panel for a Flux live-serial K6. It is **not**
a radio emulator: it renders native 128×64 LCD screenshots from the connected
radio and forwards logical keypad events through the existing
`tools/k6_live.py` CLI. There are no writes to radio flash, no reset/loader
transition, and **no transmit/PTT endpoint**.

## Run it

Prerequisites: a K6 running Flux firmware with `screenshot` and
`virtual_keys` capabilities, a programming cable, Python 3.8+, `pyserial`
and `Pillow` (the same dependencies as `k6_live.py screenshot`).

```sh
cd /storage5/dev/ag7su-blowfang
python3 tools/k6_live.py --port /dev/ttyUSB0 caps
python3 tools/k6_companion.py --device /dev/ttyUSB0
```

Open **http://localhost:8776** from a browser on the same machine.

By default, the HTTP server binds to `127.0.0.1`, not the LAN. To access it
from another trusted LAN device, set a strong access token and explicitly
bind to all interfaces:

```sh
export K6_COMPANION_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
python3 tools/k6_companion.py --host 0.0.0.0 --port 8776 --device /dev/ttyUSB0
```

Open `http://<desktop-LAN-IP>:8776` and enter that token in the login dialog.
The browser holds the token only in tab-scoped session storage; the web app
never puts it in a URL. Do not expose this plain HTTP endpoint directly to
the Internet. Use a trusted network or a TLS/authenticated reverse proxy.

## Controls

- Live LCD refreshes approximately every 2.8 seconds and again after key actions.
  It captures a **rendered** screen frame, not the physical LCD glass.
- Front keys: `0–9`, `STAR`, `POUND`, `MENU`, `EXIT`, `UP`, `DOWN`,
  `VM`, `AB`, `BAND`. Side keys: `SIDE1`, `SIDE2`.
- A tap sends a short logical key event. Holding a button for at least
  600 ms sends a *single long event* when released. Alternatively,
  use the one-shot Long-press modifier or Shift-click.
- Long digit presses `1–9` are not supported by current firmware.
  It is deliberate that no PTT, power or volume control is shown as live.
- Keyboard: digits, `*`, `#`, arrow up/down, Enter (MENU), Esc (EXIT),
  A (A/B), V (V/M), B (BAND), left/right brackets (side keys),
  R (force LCD refresh).
- Save LCD snapshot exports the native resolution PNG.

The status card displays actual voltage, RSSI, VFO frequencies, modes,
and current link condition returned by the CLI. Captures and key events
are serialized inside the server to avoid competing for the programming
cable. The CLI's existing cross-process serial lock remains in effect.
A busy cable or missing live capability returns a visible error instead
of silently resetting the radio.

## Safety / architecture

The UI serves static HTML/CSS/JS without external runtime dependencies.
A Python stdlib `ThreadingHTTPServer` exposes only:

| Endpoint | Action |
| --- | --- |
| `GET /api/config` | Device and UI capabilities (no secret) |
| `GET /api/status` | Execute `k6_live.py status` |
| `GET /api/screen` | Execute `k6_live.py screenshot --scale 1`, return PNG |
| `POST /api/key` | Validate and execute one `k6_live.py key` gesture |

For LAN bind, every device endpoint needs `X-K6-Token`; cross-origin
POSTs and non-JSON POSTs are rejected. There is no arbitrary command
execution, CLI argument interpolation, file upload, tune, PTT, or firmware
operation. Physical radio power, keypad/PTT conflicts, and TX interlocks
are also enforced by the underlying firmware.

## Tests

```sh
python3 -m pytest -q tests/test_k6_companion.py tests/test_k6_live_remote_ui.py
node --check tools/k6_companion_static/app.js
```

Test the actual radio from its idle RX state by issuing MENU, taking a
screenshot, then EXIT. Never assume an emulator screenshot proves real
hardware behavior.

This app intentionally does not replace the larger
`sim/k6ide.py` browser emulator. It is an independent *physical-radio*
companion, with no changes to emulation or firmware.
