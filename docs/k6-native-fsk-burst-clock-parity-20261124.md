# Native FSK: consecutive-burst rearm, sample-clock recovery and CRC/parity erasures

**2026-11-24 · offline only · two compiled Cortex-M0 test fixtures · no physical TX or flash.**

This continues the [SoapyFang FIFO single-burst validation](k6-frequency-burst-fifo-20261124.md)
with actual **compiled** mock native-FSK transmitter and polling receiver,
behavioral FD6818B RFIC RX, synthetic RF channel, production Flux Pager/Sotto
packet framing, whitening and CRC, and production `pager.Assembler` XOR parity.
Nothing here is a firmware release gate or a new KISS/voice mode. The radio,
SDR hardware, serial port, GitHub hardware lock and queued TX-34 experiment
were untouched.

## Three distinct responsibilities

1. **FSK RX clock recovery (`sim/k6emu/rf/fsk.py`).** The old emulator
   sampled all payload bits on a fixed 8-phase grid nominal to exactly
   4,800 baud. It could lock sync with a moderate clock mismatch but the
   accumulation of phase error across a 64-byte payload corrupted the CRC.
   Now, *after initial sync acquisition*, it measures the 127 zero-crossing
   spacings in the publicly known **16×`0x55` alternating preamble**.
   A linear fit estimates the actual per-bit symbol period, compensating
   ordinary discriminator filter group delay via its fitted intercept. The
   **four-byte public sync** then resolves a possible ±1-symbol ambiguity
   in the repeating preamble. No expected payload bytes, checksum truth,
   channel metadata or transmitter clock are supplied to the demodulator.
   Payload samples are taken on the recovered fractional grid; the clock
   fit is repeated when the compiled RX C module re-arms after each frame.
   `clock_fits` and `last_acquired_clock_ppm` remain observable from the
   receiver model after completion. This is **not** physical chip behavior:
   NumPy's pilot regression is host-side research logic, not an on-radio
   implementation. PHY/SPI timing and Cortex-M0 CPU/flash budgets remain
   separate validation gates.

2. **Consecutive compiled RF bursts (`tools/k6_fsk_burst_recovery.py`).** The
   transmit half runs the existing compiled native-FSK module via Unicorn;
   it generates real Pager L64 packet bytes (`1200` codec profile, whitened,
   16-bit CRC) and one finite virtual RF burst per packet. The independent
   compiled receive module programs the simulated chip registers, polls
   IRQs, drains 16-bit FIFO words and **re-arms** between frames. After TX,
   the harness may *delete* a whole virtual burst, flip one of its payload
   bits, add noise/offset/clock error, or delay the RX FIFO polling. The
   emitted payload isn't passed to the receiver. Host-side verification
   accepts CRC-valid on-air packet sequence headers only, counts missing
   sequences, duplicates, foreign messages and corrupted packets, and
   never substitutes a neighboring packet into a hole. No CRC means no
   accepted packet. A completed RFIC frame without valid CRC is explicitly
   *not* a delivered packet.

3. **Existing XOR parity reconstruction (same CLI, `--parity`).** The tool
   additionally runs the existing `pager.Assembler` with actual L64
   depth-four interleaved data and one XOR parity packet per group (four
   data packets + one parity). It receives only emitted/recovered FIFO bytes.
   After packet CRC validation it can reconstruct **one missing/corrupted
   data packet per parity group** from the surviving packet bodies and the
   parity packet, exactly as the existing Flux packet assembler does.
   Voice-frame payloads are deterministic synthetic 6-byte slots, **not
   encoded or decoded spoken audio**. Only the scorer knows the source
   bytes, and it verifies the reconstructed message at the end. Per-slot
   good flags and reconstruction counts are exposed; two missing data
   packets in a group remain missing. FIFO overruns invalidate a qualified
   delivery even if some error correction reconstructs the payload.

## Reproduced results

| Four-packet compiled FSK link, C/N 25 dB | Old fixed 4,800-baud RX model | New public-preamble clock fit |
| --- | ---: | ---: |
| −6,000 ppm | 0/4 CRC-valid | **4/4** |
| −2,000 ppm | 0/4 | **4/4** |
| 0 ppm | 4/4 | **4/4** |
| +2,000 ppm | 0/4 | **4/4** |
| +6,000 ppm | 0/4 | **4/4** |
| ±8,000 ppm | Unqualified | **0/4**, preamble acquisition fails |

The ±6,000/0 ppm cases were additionally repeated with **three different
noise seeds at C/N 18 dB**: all 4/4 valid in the tested deterministic
fixtures. This is a test matrix, not a guaranteed sensitivity or ppm spec.
The new receiver's estimated clock offsets agreed with transmitted offsets
to within ~150 ppm in the tested cases. Extreme ±8,000/15,000 ppm remain
outside the initial nominal-rate acquisition range. Preamble edges need a
sufficiently clean signal and adequate bandwidth; a failed initial lock
cannot be repaired by later clock fitting. The algorithm currently uses a
fixed symbol-rate profile and does not implement an on-air unknown-rate
search or time-varying clock-tracking PLL.

| Compiled 5-burst loss/CRC scenario | CRC-valid data sequences | Result |
| --- | --- | --- |
| No faults | 0,1,2,3,4 | Five valid packets |
| Whole burst #2 missing | 0,1,3,4 | Sequence #2 explicitly missing; following packets recover |
| Packet #1 bit-flipped | 0,2,3,4 | One CRC-invalid frame; following packets recover |
| Burst #1 absent, burst #3 corrupted | 0,2,4 | Two holes retained, no inventing data |
| Delayed 25 ms FIFO polling | Not qualified | FIFO overrun; cannot report complete delivery |

| Existing parity mode, four data plus one parity | 6-byte voice-frame slots | Reconstruction |
| --- | ---: | --- |
| Clean | 32/32 | None needed |
| Drop one data burst | 32/32 | One packet rebuilt |
| Corrupt one data burst | 32/32 | CRC rejected; one rebuilt |
| Drop two data bursts in same group | 16/32 | Not recoverable, failure |
| Drop one data burst and keep parity, at −6,000 or +2,000 ppm | 32/32 | One rebuilt |
| Drop one in each of two independent parity groups | 64/64 | Two rebuilt |
| RX FIFO overrun under 25 ms polling | Not qualified | Fail even with parity enabled |

**Caveat:** the system currently models only fully preloaded finite native-FSK
bursts; continuous FIFO refill remains a separate hardware and firmware
qualification question. The radio chip's true clock-recovery silicon,
receiver filtering, AFC, CRC and packet FIFO behavior have *not* been
measured by this virtual model. Recovered payloads are from the compiled
mock TX/RX harness, not a flashable live half-duplex Sotto release.

## Run the experiments

```sh
# Compiled TX and RX, five consecutive packets, one erased RF burst
python tools/k6_fsk_burst_recovery.py \
  --packets 5 --drop 2 --json /tmp/flux-fsk-gap.json

# Public preamble/sync clock recovery with +6000 ppm TX clock mismatch
python tools/k6_fsk_burst_recovery.py \
  --packets 4 --clock-ppm 6000 --json /tmp/flux-fsk-clock.json

# One dropped data packet + parity reconstructed, full message exact
python tools/k6_fsk_burst_recovery.py --parity \
  --packets 4 --drop 2 --json /tmp/flux-fsk-parity.json

# Negative: two erasures in same parity group (nonzero exit with JSON)
python tools/k6_fsk_burst_recovery.py --parity \
  --packets 4 --drop 1 --drop 2 \
  --json /tmp/flux-fsk-two-lost.json

# Relevant tests (host needs Unicorn, NumPy, clang/LLD toolchain)
python -m pytest -q tests/test_k6_fsk_burst_recovery.py \
  tests/test_k6_fsk_parity_recovery.py \
  tests/test_k6_virtual_link.py tests/test_k6_fsk_rf.py
```

## Next gates

- **Modem selection:** This validates finite native-FSK packet timing, public
  clock recovery and the existing parity assembler, not FSK FIFO refill,
  KISS ABI on actual hardware or continuous streaming. Keep feature gates.
- **Firmware design:** Port a *bounded, integer-efficient* public-preamble
  clock fit to a K6 module if the receiver can expose a suitable stream;
  host NumPy correlation/regression itself is not Cortex-M0 flashable.
- **Live Sotto:** Integrate packet sequence/CRC/parity good flags into the
  flashable half-duplex sender/receiver when its RAM, SPI and size limits
  are resolved. Do not let a loss of two packets sound like valid audio.
- **Bench evidence:** [TX-34](https://github.com/tcarwash/flux_radio/issues/37)
  remains queued for physical phase/frequency transient measurements. Follow
  the project's trusted-owner hardware queue and bench lease. No physical
  experiments were started by this work.
