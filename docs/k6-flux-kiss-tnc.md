# Flux mode-aware KISS TNC module

OFFLINE, buildable K6 Flux loadable module: raw serial KISS on the
programming-cable UART while it runs (API level 14). No radio/serial/SDR,
flash, or transmit is touched by the build or the host tests.

Files:

- `c/k6/mod/fluxtnc.c` — the module (`K6_MODULE_LEVEL 14`, `K6_MODULE_NAME("FLUX TNC")`).
- `c/k6/mod/flux_kiss_mux.h` — protocol constants, bounded KISS parser,
  SET_HARDWARE encode/parse helpers, backend registry. Pure C, host-tested.
- `tests/test_k6_flux_kiss_module.py` — host ctypes unit tests plus the real
  Thumb relocatable build check.

## Wire protocol (fixed for the parent host bridge)

KISS framing: `FEND 0xC0`, `FESC 0xDB`, `TFEND 0xDC`, `TFESC 0xDD`, bounded
escaped packet, port 0 only. Command byte `(port << 4) | cmd`; other
ports/commands are ignored and never key.

- Type `0x00` DATA: AX.25 bytes WITHOUT FCS, 17..75 bytes, exactly what
  `ap_tx_encode` (`c/k6/mod/aprs_tx.h`) expects for the APRS backend. The
  host owns the callsign bytes; the module never rewrites them.
- Type `0x06` Flux SET_HARDWARE: payload `F X version op args...`,
  version 1. Ops: `LIST=1`, `GET=2`, `SET=3`, `CAPS=4`, `STATUS=5`.
  Responses are type `0x06` with data `F X 1 op|0x80 status body...`.
  Status: 0 OK, 1 malformed, 2 unknown, 3 unsupported, 4 busy, 5 IO,
  6 too_long, 7 radio_refused.

| Op | Args | Response body |
| --- | --- | --- |
| LIST | none | one frame PER mode: `mode_id u16le, flags u8, max_payload u16le, name_len u8, ASCII<=12`; then terminator `mode_id=0xFFFF` with zero fields |
| GET | none | selected mode id `u16le` (0 = none) |
| SET | mode id `u16le` | selected mode id `u16le` (selection only, never transmits) |
| CAPS | none | `max_rx u16le, max_tx u16le` (rx is 0: no native receive) |
| STATUS | none | `tx_success u16le, tx_fail u16le` (saturating) |

Mode 1 is APRS 1200 DATA, flags `0x01` (TX only; no `0x02` RX bit without a
native receiver, never `0x04` voice-as-data). It is listed only when
`api->afsk_tx_send` is present; otherwise LIST returns just the terminator
and SET answers unsupported.

## TX chain (mirrors `c/k6/mod/aprsbeacon.c`)

DATA with mode 1 selected: `ap_tx_encode` first (length re-checked, at most
1024 bits for the level-13 call), then current `rx_hz()` must be in a ham TX
band (144–148 / 420–450 MHz), two RSSI idle samples (busy above −82 dBm),
bounded `tx_on(3000)`, `afsk_tx_send`, `tx_off` always, no retry. Malformed,
oversized, unselected, or unsupported frames return before `tx_on` and never
key. Host KISS DATA is the explicit TX request (no UI step); SET never
transmits. Counters feed STATUS; DATA has no per-frame reply.

## Runtime shape

Bounded UART drain (128 bytes/turn), parser drops oversized frames, invalid
escapes, escapes cut by FEND, empty and type-only frames, and resyncs on
FEND. Ends on key/power-off or after 1 h (`FLUXTNC_RUN_MS`); always
`tx_off` if keyed and `kiss_mode(0)` on exit. All state in scratch
(`fluxk_rx_t`, `ap_tx_t`, one frame buffer); no writable statics, no
division. `cancel_poll` (level 25) is NOT used — it is documented as
incompatible with level-14 exclusive KISS mode.

## Registry / API26 gap

`fluxk_backends[]` is the dispatch table: one entry per installed modem
conforming to the Flux packet shape, found via `fluxk_backend_find`. API
level 14 exposes no descriptor for discovering installed providers (nothing
to enumerate without inventing availability or name-sniffing), so only the
native mode is listed. A future ABI descriptor (the "API26 gap") can append
entries here without touching the dispatch logic. Sotto voice visibility
needs installed-module discovery (separate agent's task) and is NOT faked
under this Data TNC.

## Exclusive-UART limit

`kiss_mode(1)` takes the programming-cable UART exclusively; diagnostics and
any concurrent serial user are unavailable while the module runs. If the host
cannot reach `kiss_mode`, the module refuses (`-4`) and stays down.

## Build and test (host, offline)

```sh
python3 -m pytest tests/test_k6_flux_kiss_module.py -q
```

Covers: KISS round-trip with escapes, invalid-escape/oversize/type-only
drops with resync, SET_HARDWARE parse/validate/response layouts, the DATA
TX gate matrix (short/long/unselected/no-AFSK never key), band edges, the
single-entry registry (TX-only flags), `ap_tx_encode` against
`modem/aprs.py:hdlc_bits` bit-for-bit, and `modem.k6mod.build` of
`fluxtnc.c` (clang `-fropi`, two-address identical, level 14, name block).

## Optional matched API26 installed-mode discovery

K6_MODE_REGISTRY=1 adds a *read-only* API26 callback which can enumerate
an installed SOTTO TEST module as a VOICE-only mode with zero payload.
For safety, the generic FLUX TNC module builds with that feature OFF:
existing API29 firmware may have a different extension-table layout.
Only an explicitly matched firmware and TNC compiled with
FLUXTNC_ENABLE_HOST_MODE_REGISTRY=1 will query that pointer,
and only when API level is **exactly 26**. Invalid identities, duplicate
module entries and missing callbacks cannot grant packet TX.
Voice-only KISS DATA is refused with a vendor command-6 error;
mode selection never transmits.

Cross-language host/TNC conformance tests are in
tests/test_k6_kiss_abi_roundtrip.py and ordinary host controls are in
tests/test_k6_kiss_tnc.py.
\nThe CLI also provides a remote STOP control which ACKs then exits the\nforeground TNC and restores ordinary serial ownership. The KISS TCP bridge\nissues this control on normal shutdown; it never keys RF. A lost host session\nmay require a new STOP command or physical EXIT key to restore the UART.\n