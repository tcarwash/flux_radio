# TX-17C — FD6818B direct frequency-word write while TX: repeated negative

**Physical date:** 2026-11-22 Pacific (UTC 2026-11-23).
**Radio:** Flux K6 B-chip, RFIC ID 0x4829, expected Aura module
build 95d6b260d4122287, operator-confirmed 433.500-MHz
dummy-load bench, air-coupled RTL-SDR at 1.024 MS/s.
**Source and scorer frozen in commit `3e7c646` before RF.**
See [predeclared protocol](k6-tx17c-direct-synth-protocol-20261122.md).

## Bottom line

**REG_38 low-word writes succeed, read back immediately during TX,
but DO NOT produce corresponding immediate carrier-frequency
changes on this tested B-chip under constant TXON.**

In two independent full-PA transmitted RF recordings, no requested
500 or 1000 Hz frequency changes appeared. This resembles
synthesizer latch behavior and **rules out naive rapid
per-symbol frequency synthesis by writing 0x38 alone**.
It does NOT exclude the possibility that PLL frequency changes
take effect at an RFIC state transition, that another signed
offset exists, or that direct modulation must use the
FM modulator rather than LO center-frequency registers.

## Exact procedure

1. C source `c/k6/mod/tx17c_direct_frequency_latch.c` was
   compiled as an inert default and two exact-hash-pinned
   variants. Cortex-M0 simulator checked all six ordered
   0x38 writes, unchanged 0x39, TX-on lease, PA-off/PA-on
   gates, chip/clock guards, restoration and error paths.
   Synthetic SDR frequency-step positive and latch-negative
   controls passed before actual RF.
2. PA-OFF hardware preflight (version 0) on real K6,
   accepted all six writes, with immediate matching
   readback, and reported
   `tx17c n=6 base=30725 hi=661 pa=0`.
   The live 0x38 low word **30725 = 0x7805** and
   high word **661 = 0x0295**, versus nominal
   frequency 433500000 / 10 = 43350000 =
   `0x029577F0`, a +21-word difference compatible
   with channel calibration but **not itself a
   measured absolute RF frequency offset**.
   This PA-off probe is **NOT** evidence of
   frequency modulation on air.
3. PA-ON (version 1) recorded twice on independent
   single-shot transactions; each accepted all six
   word writes with readback, returned
   `tx17c n=6 base=30725 hi=661 pa=1`,
   and restored the original module envelope
   from trusted slot 30 with verified readback.
   Both initial and final VFOs 144.390 MHz;
   TX off after each trial.
4. SDR observer detected transmitted carrier PA
   rise on each 5-second capture, with on/off
   signal magnitude ratios **25.22x** and
   **25.20x**. Analysis used the locked 35-ms
   mid-plateau exclusion, RF complex IQ frequency
   discriminator, and five unmoved control plateaus
   to subtract receiver LO offset/drift. It did
   NOT infer successful modulation from FDIC readback.

| Requested change (nominal word count) | Expected nominal RF shift | Measured run 01 | Measured run 02 |
|---|---:|---:|---:|
| +50 | +500 Hz | **+1.67 Hz** | **+4.81 Hz** |
| −50 | −500 Hz | **−0.72 Hz** | **−2.37 Hz** |
| +100 | +1000 Hz | **−4.50 Hz** | **−2.04 Hz** |

In run 01 the five reference control plateaus
varied by no more than approximately **±7.45 Hz**;
run 02 no more than **±9.2 Hz** after common
frequency reference removal. Both runs meet
the **precommitted no-immediate-latch diagnostic**
(no stepped region greater than 90 Hz and controls
less than 90 Hz) with strong observed RF,
not the immediate-response qualification.
Both measurements are conclusive under the
fixed, tested 1-kHz range/160-ms hold
conditions, not a general negative for every
PLL operating mode.

The flat steps were also not explained by a
forgotten PA-on gate: RF level remained
strong, and the recorded frequency was stable
through each step and restoration tail.

## Source integrity and archived evidence

Exact compiled code SHA256:

- PA-off `7995d322e5c8b197abc220d3716f5c9ddde4dffcd0808fba6cb89f3fde5be9b5`.
- PA-on `149a5a5a5c037b7632257d1c58b315a57eeb1918f1ca48bfd0bc0a13a8740d4b`.

Original archived full slot-30 code SHA256
`6580529c1ba552dfb9db795ce05d629ad5ac7da95231acab2bda92b349ece522`
was read/verified before and restored/readback-verified
after each physical lab transaction. No main firmware
flash, calibration flash writes or uncontrolled RF output.

All raw RF captures and transaction receipts:

`~/flux-bench/tx-injection-20261122/tone-oscillator/`

| File | SHA256 / contents |
|---|---|
| `tx17c-pll-lowword-0-run01.npy` | `f5cefd31f3e408b0c9e3ff1cdb0610e4613258777dd5829e8e5f5f6670c589b4` (PA-off) |
| `tx17c-pll-lowword-1-run01.npy` | `cff1884610f1fa6d2bff3e430500c7fa659a76bae37e4651ffbc629b941644d3` |
| `tx17c-pll-lowword-1-run02.npy` | `b882eca1fadb2d4e37ffb804904431b039edc2e37d799112c4cf63b6d8f97c53` |
| `tx17c-pll-lowword-*-run*.json` | Full hardware/SDR transaction and successful restore receipts |
| `slot30-before-tx17c-pll-lowword-*-run*.k6m` | Original signed slot envelopes |
| `tx17c-pll-lowword-1-run0{1,2}-frequency-v1.json` | Individual eight-region robust RF-frequency scores |

All files are outside Git. The analyzer and
tests are inside Git; original IQ SHA256 and
restoration receipts were checked. The two
full RF runs give **six of six commanded
nonzero changes without their expected
immediate RF offset**.

## What to try next, without repeating futile write sweeps

- TX-17D: a separate **RX-only or bounded
  state-transition** test could compare
  the *same 0x38 words* before/after
  an explicit synthesizer RXON/TXON
  re-latch. It will tell us when changes
  become physically active, but re-key
  loss of phase/settling may rule out
  coherent modulation. Do not mistake
  0x38 readback for actual RF control.
- TX-15V: test B-chip **REG_70 oscillator
  enable/gain gating** and whether
  the phase resets, freezes or runs
  through a disable period. This
  could unlock an RF phase-state
  actuator without needing an
  external MIC or losing serial.
- TX-20 APC amplitude/polar joint
  actuator, TX-16 subaudio sources,
  TX-11 native modem FIFO, TX-18
  unknown digital baseband and TX-14
  analog external reference remain
  independent candidates.

**Current assessment:** direct center-frequency word
access is demonstrated but not useful as a
**fast RF frequency DAC without state re-latch**.
The physically validated TX-15U phase steering
remains the stronger serial-only path.
