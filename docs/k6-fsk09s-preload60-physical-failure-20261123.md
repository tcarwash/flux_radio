# FSK-09S-G physical 60-word fully preloaded control — strict FAIL / root-cause discriminator PASS

Date: 2026-11-23. One bounded dummy-loaded physical FD6818B K6
transaction. The [prospective protocol](k6-fsk09s-preload60-prospective-20261123.md)
and strict public-sync-only fixed-4800-baud decoder were frozen **before RF**.

## Result

**STRICT RF TEST FAILED.** A single 60-word frame with **ALL 60 words
preloaded BEFORE TX**, and **NO mid-TX FIFO writes**, emitted an unexpected
first word `0x00F8`. The hardware-declared 60-word RF frame then contained
exactly the first 59 intended words following `0x00F8`, with its intended
60th word displaced outside the frame.

- Independently acquired one chip-generated 16×0x55 + 076b3e36 public
  sync frame, sync correlation **0.995115**, no known test payload used
  for acquisition; fixed 4800 baud, 120 payload bytes / 960 bits.
- First received word little-endian `f8 00` = **0x00F8**, not
  `0x5555`. Exact strict comparison: **281/960 bit errors**.
- **Explicit post-hoc forensic hypothesis:** `0x00F8` plus
  `expected_words[:59]` matches the received 960 bits with **0 errors**.
  This was NOT the prospective acceptance criterion and does not count
  as passing the frame.
- Module returned **60** and reported restored RX. The module was not
  trusted as RF proof; the independent IQ decoder produced the failure.
- The unexpected `0x00F8` is identical to the base `REG_59`
  control value used during setup, but its electrical or software
  source is NOT proven.

## Discriminating conclusion

This test rules out the hypothesis that an after-start FIFO write is
**necessary** for the extra leading word. No such writes occurred, yet
the exact artifact repeated. It aligns with FSK-09S-C second-frame,
FSK-09S-E and FSK-09S-F reports, in which the stray control-valued
word displaced final intended payload data. A one-word in-flight append
is separately RF-proven (FSK-09S-D, **0/784 strict bits**), so the chip's
live-write capability remains credible; this G result does not prove
that multiple-appends always work or that headroom IRQs are usable.

**Do not run another blind physical retry.** Focus next on B-specific
`REG_59` reset/FIFO sequencing, whether `0x00F8` may be inserted before
the FIFO payload, whether a prior frame/state affects it, and actual
FD6818B SPI command timing. The current repeated first-word value is
a specific source/driver clue, not proof of a particular bug. Do not
infer a safe >64-word FIFO refill or gapless native-FSK stream.

## Operator radio/SDR safety and restoration

- The FIRST attempted invocation was **aborted at the initial-state guard
  before radio TX or SPI modification**, because current VFO-A was
  440.850 MHz and older harness expected 144.390 MHz on both VFOs.
  Read-only canonical-lock preflight verified correct firmware build
  `95d6b260d4122287`, API26/cable-only, FSK RX and idle state.
- Prior to RF, revised bench source, new baseline tests and docs
  were committed as [`55bcbd7`](https://github.com/tcarwash/flux_radio/commit/55bcbd7).
  Exact observed operator baseline A **440.850 MHz** (watched),
  B **144.390 MHz**, both FM/wide/nonmemory, is frozen as a hard
  preflight and postflight match. The compiled FSK-G module **never
  changed**: source SHA256
  `c3982e2a553949472d6aef6d84d7c415dbfec3820ffc6004b6b002237ad0e27e`,
  1056-byte API3 code SHA256
  `566d23eb2211bad873e671302f3d06770a454acdca0ce40a5c66b216d2b2c217`.
- Sotto/FIFO agent explicitly ACKed shared hardware release in queue #11,
  this thread announced lease, host process/port user scan found no active
  conflicting user, and canonical nonblocking `serial_lock('/dev/ttyUSB0')`
  held for the actual transaction.
- User-approved conducted/dummy-load bench and RTL-SDR air-coupled
  capture. Hard TX watchdog 1900ms, per-frame timeout 700ms, one
  physical invocation, no auto-repeat.
- Original signed module slot30 backed up, original code independently
  restored and verified readback. Both operator VFOs restored to their
  **original distinct frequencies** and independently verified after RF.
  Final radio TX=False, standby_idle=True, watching=0.
- No base-MCU firmware flash or arbitrary calibration/SPI erase.
  Only transient signed native module slot30 replacement followed by
  verification of original code and VFO restoration.

## Evidence

Private RF archive (not committed):
`/home/tyler/flux-bench/tx-injection-20261122/fsk09s-preload60/`

- `fsk09s-preload60-run01.npy`: original RTL-SDR IQ,
  SHA256 `2e4f9468f94cf4c29beedfd70120788b2959af663773d74108e3ef7994fbd7b1`.
- `fsk09s-preload60-run01.json`: complete guarded hardware
  transaction, original module fingerprint, output verification,
  firmware completion and final radio state.
- `fsk09s-preload60-analysis-run01.json`: prospectively frozen public
  sync receiver strict FAIL, decoded full raw payload and SHA.
- `fsk09s-preload60-run01-slot30-before.k6m`: original signed
  slot envelope before temporary installation.

Reproduce without radio:
```sh
python3 tools/k6_fsk_preload60_analyze.py \
  --iq /home/tyler/flux-bench/tx-injection-20261122/fsk09s-preload60/fsk09s-preload60-run01.npy \
  --out /tmp/fsk09s-g-independent-redecode.json
```

The preceding prospective RF decoder remains **unchanged**. Its
failure is definitive under its stated acceptance criterion; the
additional 0x00F8 alignment is an explicitly post-hoc diagnostic.
