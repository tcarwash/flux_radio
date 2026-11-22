# Flux FSK-05 — Guarded native-FSK known-vector laboratory

Date: 2026-11-22. Branch: **research/native-fsk-pdm**.
Part of the [complete TX research queue](k6-tx-next-experiments.md).
The subsequent [RX-only 433.500-MHz baseline and held-out archive verification]
(k6-fsk-05-rx-preflight.md) documents the prepared capture/decoder tools
and remaining physical TX gates.

**Status:** offline vector generation, inert module and emulator-qualified
bench implementation are complete. **No physical RF transmission, firmware
flash, module installation or calibration write has occurred.**

## Files

- **tools/k6_fsk_lab_vector.py**: deterministic native-FSK FIFO vector
  generator. Rates 1200/2400/3600/4800 bits/s, gain codes 16–96, 32–64
  payload words. Also accepts arbitrary 0/1 payloads through --bits-file.
  The first bench fixture is fixed at 433.500 MHz; no other RF frequency
  can be selected in this prototype.
- **c/k6/mod/fsk_lab_vector.h**: generated static 64-word payload array
  and rate/gain/frequency constants, checked into Git for reproducibility.
- **docs/k6-fsk-lab-vector.json**: exact 1024 payload bits and byte order,
  selected settings, known segments, SHA-256 and caveats.
- **c/k6/mod/fsklab.c**: native C RAM module, ABI level 3, **inert by
  default**. Compiler only accepts an enabled variant when
  FSK_LAB_ENABLE, FSK_LAB_FIXTURE_ACK and FSK_LAB_WATCHDOG_ACK are all 1.
  That explicit acknowledgment is not a substitute for a real RF fixture.
- **tests/test_k6_fsklab.py**: host-vector checks and actual Cortex-M0
  Unicorn emulation of the enabled TX code, including FIFO writes,
  refusal paths, timeout, key abort and restoration.

## Known 1024-bit payload

| Payload bit indices | Source | Intended experiment |
|---|---|---|
| 0–255 | 0101 alternating | Sacrificial sync/settling |
| 256–319 | All zeros | First FSK frequency plateau |
| 320–383 | All ones | Second FSK frequency plateau |
| 384–447 | Repeated 00001111 | Transitions and phase slope |
| 448–511 | Repeated 11110000 | Reverse transitions |
| 512–1023 | Deterministic random tail, seed 0xACE1 | Clock/bit statistics |

FIFO wire convention: lower byte first in a 16-bit register word; bits
MSB first inside each byte. The **hardware-generated** preamble/sync
from REG_58/REG_5A–5C is unknown and is **not** included in the truth
payload. Actual transmitted bit order and preamble must be measured.

This 64-word / 4800-bit/s payload lasts nominally **213.33 ms**, not
counting hardware preamble, PLL/PA startup and control delays.

Default payload SHA-256 (16-bit FIFO words serialized little-endian):

    41a8c14cc0be749f1af3b31c13bd67bf6644a1aff7d5643e9d11de8da24f7b3e

Generate/verify explicitly:

    cd /storage5/dev/flux-native-fsk-pdm
    python3 tools/k6_fsk_lab_vector.py \
      --header c/k6/mod/fsk_lab_vector.h \
      --manifest docs/k6-fsk-lab-vector.json \
      --words 64 --baud 4800 --gain 48 --overwrite

The generator normally refuses to overwrite either output without
--overwrite; checked-in vectors should never change accidentally.

## TX register interface and guards

The C module requires ABI level 3, exact FD6818B ID 0x4829, VFO exactly
433500000 Hz, receive state, no key held, unoccupied channel by two
RSSI observations, and no conflicting FSK modem state. It invokes
firmware-owned tx_on(2600), immediately sets tx_pa(0), loads FIFO, then
sets tx_pa(1) and enables the native FSK TX modem. The core TX limit is
2600 ms and the local completion poll limit is 1350 ms. A user key
press, power switch, status timeout or any register failure aborts
through PA-off, FSK-stop, register-snapshot restoration and tx_off.

The register transaction uses the prior native B-chip lab sequence:
REG_3F=0x8000 TX work mode; REG_58=0x00e1 frame mode;
REG_70=0x0080|gain; REG_72=the baud timing word;
REG_5A/5B/5C fixed sync; REG_5D frame length;
REG_5F FIFO words; REG_59 FIFO clear/enable; REG_0C completion flag.

**REG_40 deviation is never written**. Its exact influence on the
native FSK mode remains to be characterized on FD6818B. REG_70 is
described as FSK gain in family code and accepted in a past B-chip
serial lab but is **not yet a calibrated deviation setting**.

The module never writes RF state, power/PA, or GPIO registers
REG_30/33/36/37 directly, and never writes flash/calibration. The
gated TX routine requires an **already demonstrated independent
watchdog and an actual rated dummy-load RF fixture**. The bench
acknowledgment compile macros are human assertions only.

**PA disabled is NOT RF disabled:** the guarded firmware tx_on may
briefly enable PA during transition, and residual carrier has been
observed with PA disabled. Do not execute an enabled module into
an antenna or with unverified attenuation.

Important limitation: the currently running Flux live serial
run-ram command accepts only API levels 1–2. This API-3 module
cannot be executed with that method. No installed or flashed module
has been produced in this milestone.

## Emulator verification

Unicorn was missing from the system Python environment, so it was
installed separately under /tmp/flux-fsklab-unicorn-deps without changing
the OS Python packages. The repo's sim/k6emu/requirements.txt already
declares Unicorn 2.1+.

    PYTHONPATH=/tmp/flux-fsklab-unicorn-deps \
      python3 -m pytest -q tests/test_k6_fsklab.py

Result: **20 passed, 0 skipped**. The emulated TX sequence sends
exactly the 64 expected FIFO words in order, reports completion after
the simulated send duration, restores all touched modem registers,
and explicitly releases TX. It also rejects wrong chip, band,
occupied channel, TX state, and denied tx_on; tests verify key-abort
and missing-ACK timeout cleanup. Both inert and enabled builds compile
under the module size budget (104 and 955 bytes respectively).

Extended regression suite for native FSK, PDM waveforms, archived IQ,
TX8 tone and MICIN: **62 passed** with the isolated Unicorn dependency.

This establishes *software transaction behavior only*; the emulator
does not simulate true on-air deviation, PA amplitude, RF power,
phase continuity, spurious emissions or chip framing latency.

## Real-K6 read-only hardware fingerprint

Read-only live job on /dev/ttyUSB0 confirmed:

| Register | RX value |
|---|---|
| 0x00 chip ID | 0x4829 |
| 0x30 state | 0xBFF1 |
| 0x40 FM deviation configuration | 0x3516 |
| 0x58 FSK preamble control | 0x0000 |
| 0x59 FSK control | 0x0000 |
| 0x70 FSK/tone gain | 0x0000 |
| 0x72 FSK clock | 0x3065 |
| 0x7D microphone sensitivity | 0xE920 |

The radio returned to RX, battery roughly 7.79 V, and both VFOs
remained **144.390 MHz**, the APRS shared channel. Because the
dummy-load/coupler/attenuated SDR fixture cannot be physically
confirmed, **no TX was performed** even though experimental
transmission was authorized. This is a necessary RF safety and
measurement-quality gate, not a software failure.

## Next experiment

1. Confirm an actual connected rated 50-ohm dummy load, RF coupler,
   calibrated attenuation, fixed-gain SDR capture and a tested
   independent TX watchdog/recovery path. Move to the dedicated
   **conducted** 433.500-MHz fixture, not the APRS channel.
2. Record SDR IQ before, during and after **one** short known-vector
   TX with no automatic retry. Verify exact received FIFO bit ordering,
   chip-added preamble/sync, 0/1 plateaus, transition shape, RF
   carrier drift and post-test RX restoration.
3. Perform blind frame acquisition as well as assisted correlation.
   Neither assisted bit agreement nor ideal-model waveform fidelity
   proves direct RF phase continuity or compliant bandwidth.
4. Repeat one controlled **REG_70 gain** variable at a time within
   the already tested 16–96 range, with equal RF power and fixed SDR
   settings. Resolve whether it actually reduces FSK frequency
   separation or affects another part of the modem path.
5. Keep REG_40 unchanged until a separately validated and safely
   restorable deviation recipe is established. If real phase-
   continuous, low-deviation binary FSK is confirmed, compare a
   generated 4/8-FSK frequency-DAC trajectory to captured complex IQ.
6. Preserve the other TX candidates—native 4-FSK, MICIN, MCU DAC/PWM,
   TCXO/two-point, tone, subaudio, digital I2S, PA gating and external
   modem—under the main [TX experiments roadmap](k6-tx-next-experiments.md).

No release, automatic RF test, antenna operation or continuous
transmission should be derived from this emulator milestone.
