# API31 known-Sotto packet: physical RF test (2026-11-25)

## Scope

Physical K6 FD6818B B-chip, 433.500 MHz VFO A, connected PL2303 serial cable, dummy load, RTL-SDR air-coupled. Operator authorized live tests. Shared bench owner `sotto-tone-live` was reserved before changing hardware state, and released afterward. No microcontroller firmware was flashed; the library slot swap was temporary.

## Test fixture

API31 `SOTTO 1K FX` level31, 1,340 code bytes / 1,364-byte signed-by-CRC envelope, installed temporarily at slot 27 with live-compatible legacy cable loader. Its eight 6-byte codec frames are an offline native-encoder reference generated from 320ms of 8kHz PCM16 1kHz sine. The module uses FDV1 BEGIN, VOICE, END and the same 33-word API29 async-FSK entry points used by the production Sotto transmitter. Virtual Cortex-M0 test recovered 3/3 CRC-valid frames and exact codec bytes.

Physical host build ID 5d8e7f39844d42f9, API31, cable_only, 14,976-byte arena, normal 115200 baud host control (500000 baud only during cable-loader reads). SDR Monitor at nominal 433.500 MHz with digital offset -30.8kHz.

## Physical results

- Installed API31 test envelope was read back and verified exactly. After loader reboot VFO A was retuned from the firmware's 443.500 MHz default to 433.500 MHz.
- Run 1: `k6_live.py --port /dev/ttyUSB0 run-coop 27 --allow-higher-api --timeout 15` returned `result=8`, `SOTTO 1K sent`, normal completion, without cancellation. SDR export showed zero accepted/rejected candidates, no IQ windows.
- Run 2: same module and radio state, with high-cadence (35ms) SDR status/spectrum capture. Live module again returned `result=8`, `SOTTO 1K sent`; run-command duration ~0.878 seconds. SDR signal level rose from approximately -32 dB to +1.3 dB; spectrum peak reached -4.6 dB in bin 66. Actual RF energy is confirmed.
- Run 2 receiver result: 2 CRC-rejected candidates, 0 CRC-accepted frames. Both had *zero sync-bit errors* with correlation 0.995918 and 0.995632, and tone separation 2081Hz and 2054Hz, respectively. The expected BEGIN frame was not detected.
- Event 31 matched the exact expected Sotto VOICE on-air bytes 0-55, diverging at byte 56 through the CRC area. Event 32 matched expected FDV1 END on-air bytes 0-49, diverging at byte 50 and most tail bytes (byte 51 happened to match). Both had wrong CRC; event 32 included a 2-byte alignment offset.
- Offline IQ replay over ±6000 ppm found zero valid CRCs. Offline discriminator for event 31 still showed FSK spread through the tail; event 32 had a sharp IQ amplitude collapse around bytes 54-60. The two events have different tail signatures, so exact RFIC cause is not yet proven.

### Important distinction

**The API31 serial-connected module transmit path works on hardware.** The prior API29 TX-lease refusal is avoided by targeting the current host API31. The outstanding failure is on-air payload integrity near packet termination; the Sotto wire encoder and expected synthetic packet bytes agree with the physical prefix.

Candidate faults include a premature TX-done interrupt, TX PA/RFIC cleanup ahead of the final bits, or SDR-side burst truncation. Do not treat a red TX LED, normal module return or high sync as successful delivery.

## Reproducibility and preserved evidence

Host project: `/storage5/dev/flux-sotto-api29-irq-fifo-mock-20261125`.

All physical test evidence is saved (not committed) under `/storage5/dev/sotto-monitor/evidence/bench-api31-20261125/`:

- `fixture-one/module-result.json`, `diagnostics.json`, `rejected-iq.npz`: initial successful module return with no receiver candidates.
- `fixture-spectrum/spectrum-timeline.json`, `diagnostics.json`, `rejected-iq.npz`, `replay.json`, `modulation.json`: second successful module return and two rejected packet candidates.
- `slot27-pre.k6m`, `slot27-api31-fixture.k6m`, `slot27-api31-installed.json`, `slot27-restored-target.k6m`, `slot27-restored.json`: verified reversible library swap.
- `final-diagnostics.json` and `final-rejected-iq.npz`: archived before stopping receiver.

Cleanup was verified: original `FSK DIAG` code restored to slot 27 with SHA-256 `56f2dd94c352352590907f93c8f1eb934fa44cece88eddd9957ce20cf6964bd5`, VFO A 433.500 MHz, battery 7.72V, TX false, radio idle, monitor idle, shared bench unowned. No background test or ongoing RF transmission.

## Highest-value next action

Use an instrumented API31 FSK transmitter / host that timestamps FIFO load, start of FSK, first `0x0c TX-done`, PA disable, RFIC register restore and completion of the last symbol. Compare with this same known 66-byte packet and the time-domain SDR IQ. Do not simply delay return codes or loosen band/watchdog restrictions without verifying actual FD6818B IRQ semantics.
