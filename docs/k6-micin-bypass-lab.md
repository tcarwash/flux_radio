# FD6818B MICIN waveform / TX voice-filter bypass lab

**Status:** safe live RX register fingerprint completed on one FD6818B 0x4829.
An offline Gaussian-shaped analog 4-FSK reference and a separately fixture-gated
TX register-bypass probe are implemented and emulator-tested. **No TX from
this branch, no firmware flash, no injection wiring validated.** No claim of
working direct 4-FSK over RF yet.

## Why this approach

The K6's normal microphone connects to the RF chip's MICIN. Its MCU-generated
PA11 PWM goes via a filtered speaker playback path, **not MICIN**. The old
FD6818 REV 1.0 guide specifically describes transmitting shaped 4-FSK
through MICIN when configured for DMR/dPMR bypass, and disabling MICIN DC
cancellation. That is a lead, not a safe B-chip register sequence.

The FD6818B driver *already used in Flux* describes a more actionable
per-chip TX voice-filter control:

- `REG_AF_FILTER=0x2B`, bit0 = disable TX pre-emphasis;
- bit1 = disable TX LPF1;
- bit2 = disable TX 300 Hz HPF;
- RX filters live in bits 8–10; preserve them.

The driver writes `0x0300` to 0x2B upon RX entry, consistent with the live
capture below. A bench transmitter can RMW the low three bits to obtain
`0x0307` while preserving the receiver bits, then restore the exact
previous value. **This is driver source evidence, not yet measured RF
bypass behavior.** Also, disabling filters may amplify out-of-band products.
An RF spectrum and mask check is mandatory before on-air use.

`REG_7E[7]` controls DC-cancel bypass in the *older* FD6818 guide, but
its map and actual effect on the FD6818B are **unproven**. Current Flux B
driver initializes 0x7E to `0x303E`. The bench module intentionally does
**not write 0x7E**. Do not simply copy the older 0x4819 bypass example.

## Physical probe — November 21, 2026

With the radio on VFO A 146.560 MHz, FM RX, 7.17–7.18 V, no transmission
in progress, this RAM-only module was executed on /dev/ttyUSB0:

```sh
python3 tools/k6_live.py --port /dev/ttyUSB0 caps
python3 tools/k6_live.py --port /dev/ttyUSB0 run-ram \
  c/k6/mod/txpathid.c --cable-only --timeout 10
```

It returned 24 read-only register values and exited normally. Important
values (all hexadecimal):

| Register | RX value | Why |
|---|---|---|
| 00 | 4829 | FD6818B identity |
| 2B | 0300 | Three TX bypass bits currently clear |
| 30 | BFF1 | RX state |
| 40 | 3516 | B-driver FM deviation setting |
| 43 | 3028 | Wideband configuration |
| 47 | 6040 | Quiet/squelched AF |
| 7D | E920 | Microphone sensitivity |
| 7E | 303E | Candidate DC-cancel control, semantics unverified |
| 70,71 | 0000 | Tone generator disabled |
| 58,59 | 0000 | Native FSK engine idle |

Full host capture: `/tmp/flux-txpathid-expanded-20261121.json`.
The output and before/after live status confirmed idle RX at 146.560 MHz.
No chip writes, PTT, flash or calibration.

## Offline shaped 4-FSK stimulus

```sh
python3 tools/k6_micin_4fsk.py \
  --out-dir /tmp/flux-tx-micin-baseband-20261121 \
  --n-symbols 128 --symbol-baud 1200 --amplitude 0.03
```

Outputs:
- `micin-4fsk.wav`: 48 kHz / 16-bit mono **signed baseband
  frequency-control levels**, symbol alphabet -1,-1/3,+1/3,+1,
  16-symbol fixed sync and seeded payload, Gaussian-shaped at BT 0.5;
  40 ms zero guard on either side, ~187 ms total.
- `micin-1khz.wav`: 1 kHz amplitude-calibration tone with gentle fades.
- `micin-4fsk.json`: seed, exact symbol IDs, normalized intended levels,
  gain and timing, so an SDR can align known symbols with measured RF.

**These are not audio FSK tones**. They must be presented as analog
instantaneous-frequency-control voltage at a suitable TX modulator
input. The DAC and jack may high-pass DC. At 1200 baud some short-term
shaping could survive AC coupling, but long plateaus and fine, slow
frequency trajectories may not. Sample value `0.03` means 3% *WAV
full scale*, not 30 mV or a calibrated RF deviation.

## Legacy filter-only TX probe retired

The earlier c/k6/mod/txbp4.c bypass probe was shown to be **incapable
of testing MICIN**, because the normal module TX path turns its ADC OFF.
The module remains inert by default, and a TXBP_ENABLE=1 build now
**fails compilation**. Do not use it to key the radio. Its replacement
is the separately gated level-26 c/k6/mod/txmic4.c experiment.

PA-off is not RF-off: the initial guarded tx_on() can briefly enable
the PA before a module disables it. Both TX-capable prototypes are
bench-only, require a rated dummy load and an isolated RF pickoff, and
are not approved for open-air testing.

## Next integrated hardware experiment

1. Map the actual K6 microphone jack + programming cable electrical
   circuit from the matching board schematic or multimeter. The
   programming cable can occupy or bias microphone wiring; use a
   reversible, isolated mic-injection adapter. Never connect PA11 PWM
   directly to MICIN, the PA output, or a headphone power stage.
2. With no RF TX, confirm source voltage, DC bias, impedance and
   AC coupling at the isolated adapter, and scope the shaped 4-FSK
   waveform. Respect the MICIN operating level specified for the
   actual B chip; the old-chip guide's 200 mV warning is not a
   validated B-chip maximum.
3. Fix/verify a genuinely independent firmware/hardware TX timeout;
   instrument a **conducted** test with dummy load, coupler,
   attenuation and SDR. Confirm both bypass-register restoration
   and unkeyed RX state after every shot.
4. Start with the 1 kHz calibration tone; measure RF deviation and
   baseline spectrum. Then capture the exact 4-FSK sequence with
   filters enabled vs bypassed, using the *same* analog signal and
   gain. Check symbol levels, edge response, frequency drift,
   harmonics/spurious power and occupied bandwidth.
5. Only after the B chip's proper MICIN TX path and register semantics
   are measured: test whether TX DC-cancel bypass is necessary,
   then isolate any additional hidden digitally driven or
   TCXO-based modulation route.

## References / provenance

- Running B-chip driver source in
  `firmware/k6-aura/build/aura-fd6818b-port/src/drivers/fd6818b.rs`
  (generated from pinned Aura source in an independent worktree,
  not modified in this experiment): AF filter mapping and TX/RX
  state transitions.
- Flux source `docs/k6-fd6818-audio.md`, `docs/k6-native-fsk-pdm.md`,
  and `docs/k6-pcm-tx-path-research.md` from the separate
  `research/pcm-tx-path` worktree.
- Manufacturer original (non-B) guide:
  https://www.glaver.org/blog/UV-17/FD6818.pdf
- RadioFoundry DMR bypass hardware comparison:
  https://radiofoundry.org/docs/spec/hardware-dm1801pro

**All new source and tests are on `research/native-fsk-pdm`.**
Do not merge a TX bypass mode into release firmware without actual
RF and independent-timeout verification.

## Receive-only TX-filter validation — November 21, 2026

The 606-byte API-level-2 module c/k6/mod/txbprxcheck.c ran successfully on
the physical FD6818B (0x4829). It refuses key-held, wrong frequency, wrong
RX state, and wrong baseline. At 146.560 MHz FM it performed exactly two
REG_2B writes, changing 0x0300 to 0x0307 and back, verified each readback,
and verified that RX state REG_30 and DC candidate REG_7E did not change.
It did not key, enable PA, flash firmware, touch GPIO or alter frequency.
Report: /tmp/flux-txbprx-20261121.json. Final status remained RX,
transmitting=false, battery 7.21V. This proves writable/restorable filters,
NOT the TX audio response.

## Critical finding: default module TX disconnects MICIN

The real generated B-chip module host uses MOD_TX_STATE=0xC1FA, with
REG_30 bit 2 (MICIN ADC) OFF. Thus the older txbp4.c filter-bypass probe
cannot itself transmit a waveform from the microphone connector. The
ordinary Aura transmit state with MICIN enabled is 0xC1FE. To exercise
external shaped 4-FSK, Flux needs an explicit dedicated MICIN path.

The experimental opt-in firmware patch apply-micin-tx-api.py appends API
level 26 callback micin_tx(enable), gated by K6_MICIN_TX=1. The build
requires live serial and the independent TX watchdog. The callback
refuses unless a guarded module TX is already active, watchdog ready,
FD6818B verified, and REG_30 in a known TX state. Enabling MICIN also
requires a confirmed PA-disabled REG_36 value. It changes only REG_30
bit 2 and reads back the exact result. The ordinary module API and
normal build remain unchanged. This is unflashed research software.

For real RF work we still need a verified adapter into MICIN, analog
bias and coupling measurements, load + coupler + SDR attenuation,
measured emissions, a synchronized waveform, and hardware-safe timeout.

Useful related work: Mobilinkd's Quansheng UV-K6 BK4819 (0x4819) flat
digital mode used bypassed TX AF/DC/ALC/AGC filters and modified the
analog board for low-frequency flat baseband, plus handled MICIN bias.
It is precedent, NOT a proven B-chip (0x4829) register/board mapping.
References:
https://github.com/egzumer/uv-k5-firmware-custom/pull/583/files
https://github.com/mobilinkd/uv-k6-digital-mod
https://hackaday.com/2024/07/05/hacking-a-quansheng-handheld-to-transmit-digital-modes/


## Offline compiled MICIN firmware — November 21, 2026

The experimental opt-in firmware has now **compiled successfully**, without
flashing, radio access, or creating a transmit waveform on-air. Its build
uses K6_LIVE_SERIAL=1, K6_LEAN_TXDOG=1, K6_RAM0=stack,
K6_SERIAL_TRANSPORT=1, K6_SCAN_SERVICE=0, and K6_MICIN_TX=1.
Image: /tmp/flux-micin-lab-firmware/flux-fd6818b.bin. It is NOT a release
image and should not be flashed without matching baseline/fixture validation.
The independent TXDOG Rust code is compiled in; this does not prove the
actual board can recover from every fault or that the MICIN test signal
reaches the RF chip.

The new c/k6/mod/txmic4.c RAM module is intentionally inert when built
normally. The enabled bench-only build separately requires TXMIC_ENABLE=1,
TXMIC_FIXTURE_ACK=1 and TXMIC_WATCHDOG_ACK=1; it declares API level 26.
It refuses wrong silicon/frequency/busy channel/key-held, requests bounded
TX, immediately turns PA off, enables MICIN ADC through the firmware-owned
callback, sets TX voice-filter disable bits, allows 250ms for possible MIC
bias settling, briefly enables PA for 100ms (under 750ms module TX limit),
then disables PA, restores the filter, disables MICIN and calls tx_off.
This has only compiler and emulator-inert/guard test evidence so far.
It is not a complete synchronized live waveform-transmit harness: an
external properly conditioned source, coupled SDR measurements and a
fixture are still needed before the enabled version is run physically.

**Important:** normal module TX uses MICIN-off REG_30=0xC1FA; only the
separate level-26 path may request MICIN-on 0xC1FE, and its callback
requires PA disabled at the moment MICIN is enabled. The new callback
is not exposed by the radio's current firmware.


## Compiled-image emulator acceptance (same research configuration)

With K6_SIM_LIVE_IMAGE pointing at the compiled 99KiB MICIN-enabled
image, tests/test_k6_live_control_sim.py completed: **7 passed,
1 skipped, 1 failed**. The failed
test_compiled_live_coop_cancel_token received an unsupported-command
error from the compiled-image emulator. This may be a pre-existing
configuration/feature interaction; **it has not been isolated or
resolved**. Do not treat the experimental firmware as release-ready.
All MICIN-specific host ABI patch checks and inert-module compile gates
passed, but no on-radio MICIN callback or transmitted 4-FSK was tested.
