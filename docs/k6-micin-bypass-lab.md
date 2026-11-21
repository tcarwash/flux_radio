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

## TX register-bypass prototype (NOT authorized for live use yet)

`c/k6/mod/txbp4.c` is intentionally **inert** when compiled normally.
The separately opt-in bench build requires both `TXBP_ENABLE=1` and
`TXBP_FIXTURE_ACK=1`, and refuses incorrect chip, frequency, busy
channel or key held. It requests a bounded TX window, immediately
disables PA, sets only 0x2B TX-filter bypass bits, waits 200 ms,
restores 0x2B and calls `tx_off`. The Cortex-M0 emulator tests verify
normal path and abort/refusal cleanup. The module itself has **no
PCM playback path**, and the B-chip bypass is **unvalidated**.

PA disabled does **not** mean zero RF leakage; `tx_on` can initially
enable PA before `tx_pa(0)`. Do **not** run the enabled build
without a properly rated 50-ohm dummy load, attenuated SDR/coupler
and verified independent TX timeout. A prior lean firmware TX
watchdog was a no-op; software polling is not a hang recovery.
The current 200 ms hold also needs controlled host audio playout
synchronization for a meaningful test. No module in this branch
attempts TX automatically.

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
