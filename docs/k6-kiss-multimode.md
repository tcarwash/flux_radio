# Flux multimode KISS TNC — Data pack

This development feature introduces a small loadable KISS TNC, a serial
host utility, and a common mode/packet contract. It is **not** a new
Data2G HF modem, OFDM/ARQ implementation, or a promise that every old
library module can send arbitrary packets.

## Placement and ownership

The optional FLUX TNC module belongs in Data. Its raw serial support
already exists at module API level 14; keeping its parser and AX.25
framing in the loadable module avoids spending Base firmware flash.
The radio must explicitly launch FLUX TNC from Catalog. During that
module run, the programming UART is exclusively in KISS mode; live
serial, programming, CAT and other serial diagnostics cannot share it.
A companion program at tools/k6_kiss_tnc.py provides a single-client
localhost KISS TCP bridge or an interactive mode-control CLI.

## Wire format — Flux extension version 1

Standard KISS FEND 0xC0, FESC 0xDB, TFEND 0xDC and TFESC 0xDD framing.

- KISS type 0x00 is port-zero DATA (FCS-stripped AX.25 for APRS).
- KISS type 0x06 is SET_HARDWARE. Recognized Flux extension bodies
  start with bytes 0x46 0x58 0x01 ('FX', version 1) then an opcode.
  Unknown hardware commands must be harmless.
- Ports other than zero are unassigned in v1 and cannot be silently
  reinterpreted as port zero.
- No automatic RF on entering KISS, listing modes or changing modes.
- Responses use type 0x06 with body 'FX', 1, opcode|0x80, status,
  followed by the reply payload.
- Status: 0 OK, 1 malformed, 2 unknown mode, 3 unsupported, 4 busy,
  5 IO, 6 oversize, 7 RF refused.

Commands:

| Op | Name | Request body | Success reply body |
|---|---|---|---|
| 1 | LIST | none | one descriptor per mode, terminated by mode=0xffff |
| 2 | GET | none | selected mode ID, u16 little-endian |
| 3 | SET | desired mode ID, u16 LE | selected ID, u16 LE |
| 4 | CAPS | none | max RX, max TX, two u16 LE |
| 5 | STATUS | none | TX success and failure counts, two u16 LE |

LIST descriptors: mode ID u16 LE, flags u8, max payload u16 LE,
name length u8, ASCII name up to 12 bytes. End marker has ID 0xffff,
zero flags/MTU/name length. Capability bits: 0x01 PACKET_TX,
0x02 PACKET_RX, 0x04 VOICE. These indicate tested capabilities, not
an assumption based on a module name.

First backend: mode 1 APRS-1200 with bounded guarded Bell-202 packet TX
using existing AFSK host callback and AX.25 FCS + bit stuffing.
Maximum initial AX.25 frame is 75 bytes (without FCS); RX is not
advertised until a real compatible receive path is integrated.
Mode 0x0100 is reserved for SOTTO-1200 voice, but it is only listed
when an appropriate installed module can be verified; it is VOICE
only until an FDV1 arbitrary datagram TX/RX adapter is implemented.
A voice-only mode must refuse KISS data and never key RF for it.

## Installed modems and the next ABI

The existing foreground modules share ONE code/stack arena and cannot
run nested inside a KISS foreground module. The generic installed-module
packet callback contract therefore needs a separately verified host
mailbox and provider entry point, so the host can suspend the TNC,
load the appropriate module, transmit its payload, and safely resume.
A stable numeric mode ID, descriptor and packet capability must be
explicitly registered by each modem. An SPI module name or catalog
label alone does not establish packet compatibility.

The Data TNC's initial APRS transport and mode API are deliberately a
first backend; arbitrary old modules cannot be made packet-capable by
merely installing this feature. Sotto is a selectable voice mode first,
not automatically an AX.25 modem.

## Host use after installing/running FLUX TNC on the radio

    python3 tools/k6_kiss_tnc.py modes
    python3 tools/k6_kiss_tnc.py get
    python3 tools/k6_kiss_tnc.py set APRS-1200\n    python3 tools/k6_kiss_tnc.py stop
    python3 tools/k6_kiss_tnc.py serve --tcp-port 8001

The TCP server defaults to 127.0.0.1, one KISS client, and bounded
queues. LAN exposure requires --allow-lan and has NO authentication.
Stop the bridge before live programming or other serial operations.
Host code never silently launches or installs a module.

Offline tests (no physical serial or transmission):
    python3 -m pytest -q tests/test_k6_kiss_tnc.py

Physical acceptance still requires Thumb relocation, RAM/scratch
budget, raw UART restoration, negative parser/oversize/mode tests,
independent AX.25 reference checks, and an independently approved
one-shot dummy-load bench lease and restoration evidence.

## Optional API26 discovery build and compatibility boundary

The optional firmware extension enabled by K6_MODE_REGISTRY=1 exports a
read-only mode_info(index,out) callback at API26. It inspects verified
installed packed-library metadata, and returns SOTTO TEST (0x0100,
VOICE-only, zero packet MTU). SOTTO RFTEST is excluded unless the
separate K6_MODE_RFTEST=1 experimental flag is set. It never launches
or runs another module, never transmits, and cannot accept KISS DATA.

The C KISS TNC deliberately compiles this optional callback out by
default. Existing development radios may advertise API29 but assign
different functions to extension-table slots; reading an unknown
API26+ callback there would be unsafe. For a *matched* newly built
API26 firmware image, build the TNC with
FLUXTNC_ENABLE_HOST_MODE_REGISTRY=1 and check the exact radio build
identity before installation. The TNC will then invoke mode_info only
when api.level==26, not just because it is >=26. The generic Data pack
build remains compatible with API14+ and advertises only native
APRS-1200 packet TX until an explicitly matched registry variant
is installed.

Compiler-verified combined Aura image (offline, no flashing):
- K6_FSK_RX_SERVICE=1
- K6_VFO_PROBE=1
- K6_VFO_SPI_PROBE=1
- K6_VFO_SELECTOR=1
- K6_SOTTO_VFO=1
- K6_MODE_REGISTRY=1

With the pinned Aura revision and submodule initialized, this variant
builds to 111,880 bytes (120KiB application limit) and uses an optional
read-only SPI mode registry. The default Data TNC also passed
thumbv6m-none-eabi compilation with its optional registry macro
explicitly enabled.

**Still not implemented:** an arbitrary installed packet modem must
define a canonical bounded packet ingress, ownership/handoff, timeouts,
TX/RX result and cleanup contract. Foreground code shares one arena,
so the TNC cannot simply call another module while itself running.
The host must stage a message in a trusted mailbox, release the
foreground TNC, run the selected provider, then resume the TNC and
return its result. Until that cross-module ABI is built and tested,
other installed modems must not advertise packet TX/RX in KISS.
Sotto voice selection is presence-aware but deliberately PTT-inhibited
until its own live transmit mode is qualified.
