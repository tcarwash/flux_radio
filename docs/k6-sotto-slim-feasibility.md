# Sotto slim encoder for Flux microphone/digital voice

Status: audited and host/Cortex-M0-benchmarked on November 21, 2026.
Branch: research/mic-digital-voice. This research branch alone advances
the sotto submodule to upstream commit
598f1bce87c22ebada2b9d0d1fcfd89107b33a09 (Nov. 20).
No firmware flash, radio transmission, or physical microphone recording.

## The news: upstream Sotto solved the big encoder cost

Upstream merged the slim Sotto 1200 encoder on Nov. 20:
- sotto/c/fixed/sotto_fx_slim.c: fixed-point CPU/space efficient
  analysis and two-path/cell-indexed encoder
- sotto/c/fixed/sotto_slim.h: stream 80 samples of 8 kHz PCM each
  10 ms through slim_encode10; emits 6-byte Sotto 1200 superframes
- sotto/c/fixed/slim_cells.h: cell indexes to limit vector comparisons
- sotto/tools/cm0_slim_build.sh and sotto/c/fixed/cm0/run_slim.py:
  Cortex-M0 binaries and instruction/cycle model
- sotto/docs/sotto-slim.md and sotto/tests/test_slim.py:
  quality scores and portable golden vectors

The ordinary Sotto 1200 decoder remains compatible. Upstream's
held-out 35-voice PESQ was 2.678 for slim versus 2.751 for full:
a small average drop for dramatic CPU and memory savings.

## Independent benchmark on Flux developer host

Upstream commit 598f1bc, Ubuntu clang 21.1.8, -Oz, zero-wait RAM
Cortex-M0/Unicorn timing model, 240 ms of male_libri3436_15s.
The 8 kHz source was converted to signed PCM16. Six 40 ms blocks.
The native and M0 outputs matched byte-for-byte: 36 bytes and
SHA-256 54391ad9a966b5aa218675ef487a4f6b2036477c29235b9e4cf2f1cea6dfa5ba.

| Quantity | Independent measurement |
|---|---:|
| Mean CPU equivalent (40 ms block) | 23.16 MHz |
| Worst of 6 blocks | 25.85 MHz |
| State | 3,148 B |
| Modeled stack high-water | 704 B |
| Linked M0 image (includes codebook) | 41,212 B |
| Shape codebook in that image | 30,720 B |
| Code and tables excluding codebook | approximately 10,492 B |

Upstream's larger sample set gives 27 MHz mean / 29 MHz worst for
-Oz in RAM, 676 B stack, and 3,148 B state. The local 240 ms test
validates bitstream identity and short-segment CPU cost, not
sustained radio encode or RF packet delivery.

To reproduce the model, install the Unicorn, Capstone and NumPy
Python dependencies, then run:
    cd sotto
    make -C c fixed/sotto_slim_enc
    sh tools/cm0_slim_build.sh -Oz
    python3 c/fixed/cm0/run_slim.py --bits /tmp/m0.bit /tmp/voice.raw

## Flux memory is tighter than the upstream K6

Upstream budgeted a 15,408-byte module arena. Current Flux
docs/k6-live-serial.md and docs/k6-modules.md document only
11,776 bytes of active module arena in the standard live build,
shared among Thumb code, tag and scratch. Verify the actual
radio image rather than assuming 15,408.

Without the 30,720-byte shape codebook, the approximately
10.5 KB code/tables plus 3.1 KB state plus 0.7 KB stack would
not fit unchanged. Upstream added external-data hooks:
SLIM_CB_FETCH, SLIM_IVF_CEN, SLIM_IVF_CELL, SLIM_DCT.
The IVF tables (~2.9 KB) and DCT (~0.8 KB) can be moved out,
but that leaves only roughly 1 KB after state and stack,
before hook adapters, interrupt state and sample buffers.
A real linked size and stack measurement is mandatory.

The upstream Blowfang main already implements c/k6/mod/fmsotto.c:
it converts a GP0 RF-received FM signal to an 8 kHz ring and
feeds the slim encoder, with the codebook and IVF cell deck
read directly from SPI flash and DCT entries regenerated
from 21 cosine values. Upstream host tests verify that
externalized tables generate exactly the same output bits.
This is an excellent integration reference, NOT a drop-in
Flux module: upstream targets 15,408 total bytes even though
the module code itself is under 11,776. It is also for
received FM, not local microphone capture.

The radio microphone is the other remaining hard problem.
Our PA11 software delta-sigma ADC is a blocking CPU-intensive
loop. It does not follow that it can run continuously beside
a roughly 25-40 MHz encoder and maintain every 10 ms audio
block. A short store-then-encode voice message is an easier
intermediate result, but raw 8 kHz PCM occupies 16 KB/s,
requiring chunking, storage or additional memory.

Upstream sotto/docs/sotto-slim.md still states that K6 has
no internal microphone-to-MCU path. This is now outdated
for the ORIGINAL 0x4819 chip: the upstream micaf experiment
found TX-state, PA-disabled microphone audio on PA11 when
the programming cable is removed. Our 0x4829 has separately
validated receive AF on PA11, not the microphone path.

## Modem and coding budget

Six-byte Sotto 1200 superframes every 40 ms = 1200 bits/s.
The experimental 600-baud 8-tone FSK link has 3 bits/symbol:
1800 raw bit/s. With code rate 1/2, only 900 information
bits/s remain before framing; Sotto 1200 CANNOT run in real
time. At rate 2/3, 1200 bit/s leaves no framing or pilots.
At rate 3/4, 1350 bit/s leaves 150 bit/s for all overhead.

Sotto already has 600 and 400 bit/s modes and a Cortex-M0
fixed-point decoder (sotto/docs/sotto-vlr.md), but the slim
encoder here produces only Sotto 1200. Existing very-low-rate
encoders have not been proved small/fast enough for this MCU.
Options include a faster PHY, Sotto 1200 store-and-forward
slower than speech time, or adapting slim to Sotto 600.

## Host pipeline implemented in this branch

tools/k6_micvoice.py reconstructs the PA11 mic recorder's
IMA ADPCM capture to a diagnostic WAV (when physical testing
eventually works). New tools/k6_sotto_slim_lab.py converts
WAV to 8 kHz with polyphase resampling, invokes pinned
Sotto 1200 slim HOST encoder, writes six-byte superframes
and bitbudget JSON, and optionally round-trips to
decoded WAV. It is not on-device codec execution.

    make -C sotto/c fixed/sotto_slim_enc fixed/sotto_dec_fx
    python3 tools/k6_sotto_slim_lab.py /tmp/micvoice.wav --out /tmp/sotto-voice --decode

Tests cover codec API presence, bit budget, WAV format,
resampling, deterministic bitstream and decoder output.

## Next implementation work

1. Confirm the 0x4829 microphone AF-mux and TX/PA state under
   dummy load with cable-out recorder, no uncontrolled RF.
2. Reuse upstream fmsotto's codebook/cell-deck and DCT hooks,
   preserving Flux's append-only module ABI; prove it fits
   the *11,776-byte* arena with stack margin.
3. Benchmark PA11 capture and build a nonblocking sample
   handoff or short record-then-encode path.
4. Validate decoded microphone speech through existing Sotto
   receiver and PA11 speaker PWM, then integrate real TX FEC,
   framing, duty-cycle watchdog and user PTT controls.
5. Choose between faster 8-FSK PHY and slim Sotto 600 based
   on measured net payload throughput, not raw symbol rate.
