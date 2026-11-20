# Flux Hamlib integration (experimental)

Flux provides a local Hamlib NET rigctl-compatible bridge. It is not yet an
upstream Hamlib model/backend. Existing Hamlib applications can select model
2 (NET rigctl), with no Hamlib patch. Python and pyserial are required.

## Start

Build with K6_LIVE_SERIAL=1 (plus the options appropriate for the radio's
existing storage layout). For health readings add K6_HEALTH_TELEMETRY=1.
Use the correct firmware image and programming cable. Do not flash while
another bench job owns the device. Older live-v1 images work for status and
tuning; newer features require the capability-aware image.

    python3 tools/k6_rigctld.py --port /dev/ttyUSB0

Connect a Hamlib client (the first process owns the serial port):

    rigctl -m 2 -r 127.0.0.1:4532 f
    rigctl -m 2 -r 127.0.0.1:4532 F 146520000
    rigctl -m 2 -r 127.0.0.1:4532 m
    rigctl -m 2 -r 127.0.0.1:4532 M FM 15000
    rigctl -m 2 -r 127.0.0.1:4532 V VFOB
    rigctl -m 2 -r 127.0.0.1:4532 l STRENGTH
    rigctl -m 2 -r 127.0.0.1:4532 get_dcd

The daemon binds loopback only, owns the normal cross-process serial lock,
and serializes all client transactions. Use Ctrl-C before running a separate
installer, module runner or direct CLI. No daemon, forwarding or flashing
occurs automatically. Multiple network clients share one radio; the most
recent successfully accepted command wins, so coordinate them.

| Command | Current support |
|---|---|
| f/F | Get/set active VFO frequency; firmware limits and idle checks apply |
| m/M | Get/set FM, AM, USB, CW; setters require new mode capability |
| v/V | Get/select VFOA/VFOB; setter requires new VFO capability |
| t | Read actual cached TX status |
| l STRENGTH | Approximate RX signal strength in dB relative to S9 |
| get_dcd | Read RF squelch-open status (0 closed, 1 open) |
| T | Not supported, both on and off return feature unavailable |
| dump_state, chk_vfo, get_vfo_info, get_powerstat, get_info | Discovery and read status |

FM widths 8000/15000, AM 4000/8000, USB 2400 and CW 500 Hz are nominal
Hamlib mappings, not measured IF passbands. Width 0 or -1 selects the normal
mapping. CWF is reported as CW because Hamlib has no exact Flux CWF mode;
set CW selects true CW, not CWF. STRENGTH uses the firmware's existing
estimated RSSI and VHF/UHF S9 reference of -93 dBm. This is an indicative
meter, not a calibrated RF measurement. STRENGTH and DCD reject queries
when TX, sleep, scan/app mode or dual watch makes the active VFO sample
unavailable; they do not tune or wake the radio. DCD reflects RF squelch,
not tone-decoder/audio gating. PTT, other levels and level setters, split, tone, memory editing and
CW message actions remain unimplemented. Setters return errors on failure;
no implicit loader fallback or serial retries.

## Verification

Real Hamlib 4.6.5 model 2 connection tested against mocked serial hardware
over a local TCP socket, including tuning/status, mode and VFO selection, STRENGTH and DCD through the actual Hamlib client.
Packed compiled image (110,408 bytes) passed status/tune, module execution and CAT mode/VFO emulator tests (3 tests); RF
behavior, device-specific audio and real hardware Hamlib use need validation.
Remote PTT is deliberately absent until the firmware has a fail-closed
independent TX timeout/lease, explicit opt-in, safe local override and
verified behavior on disconnect/timeout. No transmit support is claimed. CAT lock queries report that the bridge has no CAT lock; this is not keypad-lock telemetry.
