# API31 serial-connected Sotto TX and Hamlib module control

2026-11-25, development branch only. No firmware flash or RF transmission was performed for these changes.

## Current API policy

The active K6 firmware exposes API31. New test modules (SOTTO TONE, SOTTO 1K FX and TXLEASE PROB) default to API31. Historic API29 physical tests remain diagnostic evidence but are not a compatibility or release requirement.

The existing native-FSK TX host already accepts API30/31 when its optional FSK service is built. It refuses API29 by design. We did not widen that permission check, override band limits, bypass power rules, or alter the independent TX watchdog.

## Two distinct TX mechanisms

Standard Hamlib NET rigctl T 1 and T 0 control leased conventional PTT. A Sotto module must separately request its own bounded module TX lease and invoke the native asynchronous FSK API. Remote PTT is not a substitute for the module lease.

The loopback-only bridge owns the serial port and serializes CAT, PTT and a new nonstandard local extension: backslash-run_module SLOT. The extension is disabled unless the host explicitly specifies a single allowed module slot, its exact installed name and current VFO frequency, and confirms the dummy load. It refuses while a PTT lease is held, when the radio is not idle RX, when the expected name does not match, or when the module is not API31. It uses cooperative cancellation and checks that TX is off after return.

Example, only AFTER consciously installing an API31 SOTTO 1K FX module in vacant or deliberately selected slot 27, connecting a dummy load, and confirming 433.500 MHz active VFO:

    python3 tools/k6_rigctld.py --port /dev/ttyUSB0 --allow-module-slot 27 --module-name 'SOTTO 1K FX' --module-frequency-hz 433500000 --dummy-load-confirmed
    printf '\\run_module 27\n' | nc 127.0.0.1 4532

The module response includes RESULT <signed result> and RPRT 0 to separate module outcome from transport success. A positive module result does NOT prove CRC-valid RF.

The currently running workstation Hamlib bridge was not restarted for this change. The real radio's slot 27 has been restored to FSK DIAG, so the above example is not presently executable.

## Qualification

The virtual Cortex-M0 and RF receiver model now advertises API31. Offline tests cover generated 1 kHz PCM, the 3-packet known-voice fixture, normal/early FSK done, Hamlib PTT leases, and opt-in bridge module-run gating. The outstanding physical CRC investigation remains separate.

Run:

    ~/.local/share/flux-rf-venv/bin/python -m pytest -q tests/test_k6_rigctld_module_run.py tests/test_k6_rigctld.py tests/test_k6_live_ptt.py tests/test_k6_sotto_serial_probe.py tests/test_k6_sotto_tone.py tests/test_k6_sotto_tone_live.py tests/test_k6_sotto_api29_overlay.py tests/test_k6_sotto_api29_native_host.py tests/test_k6_sotto_virtual_link.py

Nothing in these tests opens the physical serial device, transmits, or modifies the installed firmware.