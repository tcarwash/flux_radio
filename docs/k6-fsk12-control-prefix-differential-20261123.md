# Flux FSK frame-start control-word differential audit and DV follow-on

Date 2026-11-23. OFFLINE source/evidence comparison after the strict
FSK-09S-G and FSK-12-RF negative physical results. No new radio access.

## What the archived tests actually establish

- FSK-10B: **8/8 64-byte Sotto packets CRC-valid and bit-exact**, source
  compiled module 1466 bytes, original VFO A/B 144.390MHz.
- FSK-12-RF: four *different but valid* 64-byte Sotto packets from the
  K6 microphone, **0/4 CRC-valid**, all four RX frames begin with
  unexpected `0x00F8` then exactly the first 62 source bytes.
  Compiled module 1196 bytes; original VFO A 440.850/B 144.390MHz.
- FSK-09S-G: all 60 known words **preloaded before TX**, unexpected
  `0x00F8` + first 59 words. Thus in-flight FIFO writes are not a
  necessary condition for the anomaly.
- FSK-09S-D: standalone 49-word frame, 48 preloaded and one appended
  after TX, **full exact RF PASS**.
- FSK-09S-E/F: initial VFO A/B were both 144.390MHz, **yet both had
  the unexpected `0x00F8`**. VFO starting band may affect radio
  configuration but is **not sufficient alone** to explain the artifact.

## Control implementation comparison

Compare original `c/k6/mod/fsk_sotto_l8_burst_armed.c` (the FSK-10B
success) with the **private-only, never published**
`/home/tyler/flux-bench/micvoice-20261123/fsk12-mic-rf-private-20261123/fsk12_k6mic_private_armed.c`
(FSK-12 failure). After normalizing:
- frame count `8` versus `4`,
- the different immutable audio-data arrays,
- the human-facing status string/comment,
- an equivalent header include path/blank line,

the compiled C-level module logic is **identical**. In particular,
the complete `REG_59=0x00F8`, clear IRQ, frame length, `REG_59=0x80F8`,
`REG_59=0x00F8`, 32 `REG_5F` FIFO preloads,
`REG_59=0x08F8` start, `REG_0C` completion loop, and restore
sequence are not intentionally changed.

This is **not** machine-instruction identity; different array sizes
and optimization may cause different Thumb instruction layouts or
timings, and the FD6818B may retain state between frames. The
observed RF packet failures do **not** prove that the microphone
voice payload was corrupted before entering the TX module.

Original signed slot30 code checksum stayed the same across FSK-10B,
FSK-G and FSK-12-RF; all recorded runs restored the original radio.

## Next source/bench discriminators, in safe order

1. **Offline:** Disassemble both frozen compiled Thumb modules and compare
   register-write call order and timing-sensitive loops, especially
   `REG_59` FIFO reset/start and `REG_5F` word writes; audit actual
   B-chip `reg_write` SPI implementation. Identify a *specific* falsifiable
   hypothesis for the `0x00F8` insertion rather than blindly varying
   register magic numbers.
2. **Offline:** Compare historical successful preloaded 49/64-word
   controls, failed C/E/F/G, and the two Sotto physical runs,
   including VFO/B-state, firmware build, module code size and
   frame length. Do not silently treat their independent receiver
   framing/baud hypotheses as equivalent.
3. **Future, separate frozen hardware lease:** a pass/fail discriminator
   with a strict whole-frame public-sync-only receiver, known source
   digest, bounded watchdog, and exact slot/VFO restoration. One
   plausible candidate is a finite **fully preloaded 32-word 64-byte
   L packet** with the prior known-good eight-frame configuration,
   distinct from FSK-12's four-frame configuration. The actual
   experiment should be chosen after source audit. No blind repeat
   or automatic retry.
4. If the RFIC startup behavior is stable but always prepends a word,
   evaluate a separately versioned packet-format mitigation; do NOT
   claim existing L64 CRC transport works by post-hoc skipping a
   two-byte word. Missing final bytes include the CRC tail.

## Progress toward standalone live DV independent of this RF blocker

- Physical K6 microphone/PA11 capture and host Sotto-1200 encoder
  **work offline**, as does exact private L64 packet reassembly.
- Physical finite K6 FSK transport has previously delivered genuine
  Sotto packets (FSK-10B); current reproducibility is blocked by
  the control prefix. Current test strictly delivered **no valid
  mic-derived packet**, despite exact partial payloads.
- Sotto **slim 1200 encoder** has *already* been Cortex-M0 modeled on
  the separate `research/mic-digital-voice` branch:
  `docs/k6-sotto-slim-feasibility.md`, 23.16 MHz mean / 25.85
  MHz worst per 40-ms block in one 240-ms fixture, state 3148B,
  stack high-water 704B, ~10.5KiB code/tables excluding the 30.7KiB
  codebook. The 11,776-byte Flux module arena is too tight unchanged,
  so external SPI codebook + IVF/DCT hooks, static feature-pack
  linkage or a reserved state buffer need a measured new build.
- The next useful *native* integration gate is a short microphone
  **capture-then-encode** proof on M0 (not a full-duplex, zero-buffer
  stream), then native packet scheduling and actual receive/Sotto
  speaker PWM in separate guarded steps. The internal microphone is
  mechanically disconnected by the programming cable; autonomous
  on-radio recording/control is required for a true radio endpoint.

The larger arbitrary RF waveform research and >64-word FIFO
streaming remain separate tasks. The finite FSK packet path can
support half-duplex DV without gapless FIFO refill **once its
frame-start correctness and packet CRC reliability are qualified**.
