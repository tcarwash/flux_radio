# Live serial remote PTT (software acceptance candidate)

Remote PTT is part of the **normal Flux live serial interface**. No firmware
`K6_REMOTE_PTT` option exists, and no Hamlib `--enable-ptt` option is required.
The `ptt` capability is reported only by compatible firmware built with the
independent transmitter watchdog. Older firmware continues to reject PTT.

## Host interfaces

* `python3 tools/k6_live.py --port /dev/ttyUSB0 ptt-burst --seconds 1`:
  a bounded test. It requests an 800 ms key lease, renews every 200 ms, sends
  an explicit de-key even after an exception, and checks receiver status.
* `python3 tools/k6_rigctld.py --port /dev/ttyUSB0`:
  listens **only on 127.0.0.1** and advertises PTT to Hamlib when the radio's
  live capability says it is present. `T 1` keys, `T 0` releases; socket
  disconnect releases. `--disable-ptt` is an opt-out.

Firmware rejects invalid tokens, different owners, non-FM modes, conflicting
radio state, power-off, and transmit-disabled settings. The device itself
expires a key lease within 800 ms of the last accepted renewal (while its
foreground loop executes), irrespective of the host. A separate TX watchdog
backs up a stalled foreground loop; radio key-up is refused if the watchdog
is not ready. This is **not** a substitute for a rated 50-ohm RF load and
independently monitored physical safety acceptance.

Remote PTT uses the normal radio `set_ptt` path, so power and transmit
frequency follow the existing radio configuration rather than arbitrary
host-specified RF register writes. The API does not modify library objects,
calibration, or flash memory.

## Release boundary

Host and simulated firmware tests do not demonstrate actual RF isolation,
key-up duration on hardware, cable disconnect behavior, or physical PTT
priority. Until the physical acceptance gate passes, do not rely on this
feature for unattended transmitting. Enabling the feature in a source build
is not the same as flashing a radio. See
[the live release checklist](k6-live-release-checklist.md).

The Sotto runtime is **not part of this feature** and remains disabled by
`K6_SOTTO_SERVICE=0` (the build uses shared DSP source from the pinned Sotto
submodule).
