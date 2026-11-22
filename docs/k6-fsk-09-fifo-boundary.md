# Flux FSK-09 — physical FIFO boundary and streaming-refill study

Date: November 22, 2026. Research branch: research/native-fsk-pdm.
Related: [the TX experiments roadmap](k6-tx-next-experiments.md) and
[previous physical PDM results](k6-fsk-pdm-repeats-and-phase-20261122.md).

**Outcome:** Two fixed native-FSK frames transmit under one bounded keying
session, but introduce **46.719 ms of non-payload framing/rearm**.
An attempted **128-word single-frame FIFO refill** was safely
aborted on real FD6818B silicon: the expected almost-empty interrupt
never appeared, even though the 48 preloaded words transmitted.
No phase-continuous arbitrary-length modem stream is demonstrated.

## Test setup and safety

- Quansheng K6 / FD6818B silicon ID 0x4829, build 95d6b260d4122287.
- User-confirmed dummy load, RTL-SDR air-coupled to loaded radio,
  433.500-MHz nominal TX, 433.750-MHz SDR center, 1.024 Msamples/sec.
- Relative IQ measurements only; **no calibrated TX power or mask test**.
- The TX module is **inert unless explicit fixture and watchdog
  compile flags are enabled**. Every test used an independent firmware
  TX lease and hard deadline, user key/power abort and register restore.
- The K6's normal SPI library module slots were all occupied.
  The reversible bench tool first read/verified the *code* of the
  original slot-30 PDM test, backed up its signed envelope, temporarily
  installed one gated experiment, ran once, restored the exact original
  code with a newer signed replay serial, and verified its readback.
  Both VFOs were restored to 144.390 MHz after each experiment.
- No MCU firmware flash, calibration write or automatic RF retry.

Original trusted module code SHA-256:
6580529c1ba552dfb9db795ce05d629ad5ac7da95231acab2bda92b349ece522

Note: the original signed envelope necessarily changes its replay
serial and signature after restoration. Comparing entire envelopes
would incorrectly reject a successful restored module; the bench
instead verifies exact code, valid signed-envelope tag and attributes.

Source modules:
- c/k6/mod/fsk_boundary.c — two separate 64-word FIFO frames under
  one bounded TX lease, intentionally rearming the modem.
- c/k6/mod/fsk_refill.c — experimental 128-word single frame,
  preloading 48 words and requiring FIFO almost-empty before 32+32+16.
- c/k6/mod/fsk_fifo_probe.c — API-1 read-only FD6818B register survey
  which **does not read** destructive FIFO register 0x5F.
- tools/k6_fsk_boundary_bench.py — reversible one-shot TX and RF IQ.
- tools/k6_fsk_boundary_analyze.py — offline RF marker/interval
  analysis from existing IQ, with no radio access.

## Test A: two 64-word hardware frames; TX stays keyed

The module returned successfully:

    fsk boundary: two frames sent; RX restored
    result = 128

Raw recording:
 /home/tyler/flux-bench/tx-injection-20261122/frame-boundary/boundary-run01.npy

An independently reproducible analyzer uses two exact known FIFO
patterns, the first 512 bits of each for separate synchronization
and level fitting, and the last 512 bits as held-out tests.
**Using only the repetitive 256-bit preamble produced an ambiguous
alignment and spurious errors**; those shorter-pilot results are
not valid packet-error measurements.

| Measurement | RF observed |
|---|---:|
| Frame 1 acquisition | 0.995640 correlation |
| Frame 2 acquisition | 0.997368 correlation |
| First frame held-out bits | **0 errors / 512** |
| Second frame held-out bits | **0 errors / 512** |
| Frame 1 payload first-bit center | 0.8296615 s |
| Frame 2 payload first-bit center | 1.0897135 s |
| Payload-start separation | 260.052 ms |
| 1024 native FSK bits / 4800 baud | 213.333 ms |
| **Extra time not carrying FIFO payload** | **46.719 ms** |
| Equivalent native-bit periods | 224.25 |
| RF envelope median on either marker side | 4.138 ADC units |
| Minimum 1-ms-smoothed envelope through interval | 4.044 ADC units |
| Minimum as fraction of flanking marker | **97.7%** |
| Zero-state marker frequency before / after | -1181.5 / -1185.7 Hz |

Thus this was **not a 46.7-ms loss of RF carrier**. Its modulation
contained additional non-payload transitions while the modem
restarted, likely chip-generated framing/preamble. Envelope
continuity does **not** prove complex RF phase continuity.

Reproduce offline (choose a fresh output filename):

    python3 tools/k6_fsk_boundary_analyze.py \
      --iq /home/tyler/flux-bench/tx-injection-20261122/frame-boundary/boundary-run01.npy \
      --out /tmp/flux-fsk09-boundary-analysis.json

Saved original analysis:
 /home/tyler/flux-bench/tx-injection-20261122/frame-boundary/boundary-analysis-v1.json

**Decision:** repeating full FIFO frames under one TX lease
is not acceptable as transparent phase-accurate streaming.

## Test B: FIFO almost-empty IRQ and attempted in-frame refill

The **original** FD6818 and BK4819 register references describe
a 64-word FIFO, almost-empty threshold in REG_5E bits 11:6,
REG_3F bit 14 as enable, and REG_02 bit 14 as pending.
These are **family-level hypotheses, not verified FD6818B mapping**.

References:
https://www.glaver.org/blog/UV-17/FD6818.pdf
https://alfaexploit.com/files/BK4819V3Registers_List_20201218.pdf

A live API-1 **read-only** register probe showed
REG_5E=0x3044 while RX idle. By the older-family interpretation,
the TX almost-empty threshold would have been 1 word.
This was unproven, so the guarded test saved REG_5E and
tentatively set threshold 24 with a readback check. It used
REG_3F=0xC000, one nominal 128-word frame, gain code 21,
4800 bits/sec, 433.500 MHz and a 1900-ms independent TX lease.
It preloaded 48 words and did **not** refill unless both elapsed
time/headroom checks and the hypothesized interrupt were met.

### RF B1: no almost-empty event

The first physical attempt returned **-16** at the preset
230-ms first-refill deadline. The radio de-keyed and restored
all configuration and slot code. Relative SDR RF activity lasted
about 222 ms. The preloaded 48-word known payload decoded
**0 errors in 256 held-out bits** using a sufficiently diverse
512-bit training prefix. No further FIFO words were written.

Raw:
 /home/tyler/flux-bench/tx-injection-20261122/frame-refill/refill-run01.npy

Decoder:
 /home/tyler/flux-bench/tx-injection-20261122/frame-refill/preloaded48-trained512.json

### RF B2: post-transmission interrupt telemetry

A second one-shot module added strictly read-only sampling of
REG_02 interrupt status and REG_0C modem status. Samples were
accumulated on the module's stack and reported only **after
TX was off**:

    IRQ or=0800 last=0800 status or=0220 last=0220
    fsk refill: aborted; receiver restored
    result = -16

The old-family REG_02[14] value 0x4000 **never appeared**.
REG_02 stayed at 0x0800, and REG_0C at 0x0220, over the
sampled 230-ms wait. BK4819-family docs identify REG_02[11]
(0x0800) with DTMF/5-tone detection; **do not treat this
already-set bit as an alternate FIFO-refill grant**. A premature
append risks a full FIFO or corrupted data.

Raw capture:
 /home/tyler/flux-bench/tx-injection-20261122/frame-refill/refill-irqtrace-run02.npy

Both runs verified original slot-30 code and 144.390-MHz
VFO restoration after the abort; radio TX idle. The result
does **not** show the silicon is inherently unable to
stream—only that our assumed older-family IRQ interpretation
did not expose a usable FIFO event on this specific B chip.

## Tests and future hypotheses

All C modules are **inert by default**. The bounded emulated
success paths model a FIFO interrupt (which real hardware did
not produce); tests also confirm no-interrupt timeout, no
completion, key abort, register restoration and no unauthorized
flash writes. The boundary analyzer has synthetic signal/gap
tests and is read-only.

Next, before any more B-chip FIFO writes:
1. Audit 0x4829 register/interrupt maps and actual driver usage,
   especially REG_02, REG_0C, REG_3F, REG_5E and the timing of
   IRQ clears.
2. Design a TX-bounded **status-only** sampling experiment that
   records IRQ transitions at known FIFO occupancy without
   appending data. Distinguish a rising FIFO event from an
   always-set unrelated interrupt.
3. Verify threshold semantics and whether the FIFO can append
   while the native FSK mode is still active. Confirm all writes
   stay below actual FIFO capacity.
4. If a genuine B-specific headroom condition is validated,
   attempt one small incremental refill and analyze exact
   received I/Q, frame continuity, decoder performance and
   RF cleanup.
5. In parallel, maintain the MICIN analog baseband, native
   4-FSK/DMR, tone oscillator, subaudio, reference/TCXO,
   PA envelope and external-IQ entry points in the master
   experiments roadmap.

**Bottom line:** Bounded multi-frame TX and receive cleanup are
demonstrated. The current straightforward frame-chaining method
inserts 46.7 ms of non-payload modulation; the first
almost-empty refill attempt **failed safely**. No continuous
phase-coherent arbitrary RF streaming is yet established.
