# Flux Data-pack KISS TNC / Sotto mode — main integration (2026-11-24)

This integrates the previously separate Data-pack KISS, installed Sotto
VFO mode, packet-provider research, and hardware-test tooling. The source
merge does not automatically enable experimental packet routing on existing
radios, install modules, or flash any device.

## Feature split

- Base firmware retains the module loader and UART/AFSK APIs. Optional
  API26 read-only mode discovery and API27 128-byte packet mailbox are
  separate build-time opt-ins.
- Data pack contains the FLUX TNC installable module, bounded APRS-1200
  KISS packet TX, LIST/GET/SET/CAPS/STATUS/STOP control commands, and
  tools/k6_kiss_tnc.py serial and localhost KISS-TCP companion.
- Sotto appears in VFO mode selection only if a recognized installed
  Sotto module is found. Selection doesn't key RF; Sotto is voice-only
  until a separately qualified packet datagram provider is built.

The initial TNC has a guarded APRS TX backend. APRS RX, generic installed
modem KISS packet routing, and Sotto packet data are not yet complete.

## Firmware build variants

Default firmware builds keep the preexisting ABI and disable the
experimental provider dispatch:

    K6_MODE_REGISTRY=0 K6_PACKET_MAILBOX=0 sh firmware/k6-aura/build-fd6818b.sh

To compile the separately experimental API27 host mailbox/dispatcher:

    K6_MODE_REGISTRY=1 K6_PACKET_MAILBOX=1 sh firmware/k6-aura/build-fd6818b.sh

The latter accepts only exact authenticated LOOPBACK-EMU action35 for a
no-RF one-shot dispatch. It is not yet a generic modem TX engine.
The API27 function table differs from the lab radio's existing API29,
so the firmware and the module must be built as a matched pair.
KISS input backpressure and seamless TNC resume after a module arena
switch are unresolved. No false completion ACK or automatic retry
is permitted. The normal Data pack builds without API27 provider calls.

## Evidence / still outstanding

- Integrated focused release tests: 159 passed.
- Clean latest-main default FD6818B image: 111,760 bytes (120 KiB maximum).
- Clean opt-in API27 dispatcher image: 114,472 bytes.
- Actual dummy-loaded K6 no-RF smoke test (issue #33) passed KISS
  LIST/GET/SET APRS-1200/STATUS/STOP and live serial restoration;
  all original 31 packed modules and VFOs were independently verified,
  temporary slot30 removed, radio idle. No KISS DATA was sent.
- No physical test of generic multi-modem dispatch, APRS RF, or
  Sotto voice/packet TX is claimed. Owner will validate out of band.

## After installing and manually launching the TNC module

    python3 tools/k6_kiss_tnc.py modes
    python3 tools/k6_kiss_tnc.py set APRS-1200
    python3 tools/k6_kiss_tnc.py serve --tcp-port 8001
    python3 tools/k6_kiss_tnc.py stop

Bridge binds to localhost and takes exclusive serial ownership.
See docs/k6-kiss-multimode.md, docs/k6-flux-kiss-tnc.md,
docs/k6-packet-mailbox-v1.md and docs/k6-kiss-hil.md.
