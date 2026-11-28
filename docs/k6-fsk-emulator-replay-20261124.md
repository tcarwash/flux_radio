# Native FSK emulator: archived RF replay, 2026-11-24

## Purpose

Exercise compiled Cortex-M0 receive code against previously recorded physical
K6 RF, without claiming the hardware bench or injecting packet truth. This
extends the SDR-backed emulator introduced in 532d8b92. The new model uses
channelized IQ, the FM discriminator, preamble/sync acquisition, nominal 4800
baud symbol decisions, a bounded eight-word FIFO and polled register IRQs.
The emulator-only C fixture calls the same module API as native code.
It does not use a reference packet or CRC in its receive path.

## Reproducible configuration

Use the existing local archives from FSK-10B and FSK-12. Both are interleaved
uint8 I/Q NPY files, 1,024,000 complex samples/s, recorded nominal center
433750000 Hz. The previously measured carrier offset was about -280500 Hz
relative to the recording center. The effective center of 433780500 Hz
and model tuning of 433500000 Hz implement that known correction, not a
payload-dependent frequency search.

```sh
python tools/k6_fsk_emulator_replay.py \\
  --source npy-u8:PATH/fsk10b-l8-run01.npy \\
  --rate 1024000 --center 433780500 --tune 433500000 --duration 2.5
python tools/k6_fsk_emulator_replay.py \\
  --source npy-u8:PATH/fsk12-mic-run01.npy \\
  --rate 1024000 --center 433780500 --tune 433500000 --duration 2.5
```

No audio, IQ, private microphone payload or recovered voice is committed.
The archive SHA-256 fingerprints are:

- FSK-10B: `0da3f204fcc66a6909fdef79261003e1b954e4545293523e0564453a87ac3413`
- FSK-12: `cba7245ea7cd04f21fd5857a4d68ccbe3df9695039d8bfb6ea99a1c9e030c3d0`

## Results

| Capture | Module frames | Raw words | L64 software CRC | First FIFO word | Overflow / underflow |
|---|---:|---:|---:|---|---|
| FSK-10B, successful eight-packet burst | 8 | 256 | 8/8 | a856 (all eight) | 0 / 0 |
| FSK-12, corrupted microphone burst | 4 | 128 | 0/4 | 00f8 (all four) | 0 / 0 |

FSK-10B matched all 4096 bits from the prior independent offline blind
decoder, with zero differences in each of the eight 64-byte payloads.
FSK-12 retains the observed inserted control word; it is **not** repaired,
CRC-filtered or counted as a successful physical transmission. The earlier
physical failure verdict is unchanged. Both runs used 52 bytes of measured
fixture stack and returned after about 2500 ms of virtual time.

The independent software CRC check is applied only after the compiled
module has returned its raw frames. The receiver is not given expected
packet counts, payloads, frame start times, or CRC outcomes.

## Evidence boundary

- This is a replay of actual transmitted IQ, not new hardware testing.
- The compiled fixture is a polling receiver, not the production Sotto
  module, and the module harness does not execute the full Aura image.
- FIFO capacity is based on prior on-air RX observations. IRQ timing,
  overflow behavior, byte ordering and register latch behavior are model
  contracts, not newly measured silicon equivalence.
- Only the direct 4800-baud no-hardware-CRC 16-byte preamble/four-byte sync
  profile is implemented. No native TX, FFSK, other baud rates, hardware
  CRC, GP0/PA11 or audio codec integration is claimed.
- No hardware lease was claimed and no radio, SDR or existing workbench
  service was accessed. The physical archives remain unchanged.

Next: use this harness to run the actual production receive module when
its register configuration is compatible; add virtual TX and loopback as
a separate model, with the known corruption retained as a regression case.
