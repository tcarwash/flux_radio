# IF capture/streaming: lessons from WSPR IQ

A newer [physical IF/IQ serial lab](k6-serial-iq-lab.md) now connects the portable
mixer/CIC to GP0 DMA and binary UART output. See its measured continuity results;
the sections below retain the earlier design and qualification notes.

Reviewed `c/k6/mod/wspriq.c`, `c/k6/mod/gp0rx.h`,
`docs/k6-ft8-wspr.md`, and `tools/k6_wspr.py`.

`wspriq` **already performs continuous IF capture and serial export**:

- GP0/PB1 DMA sampling with a 1 KB ring, CPU-side draining and loss counts.
- Frequency mixing and two cascaded CIC decimators on the MCU, to about
  199 complex I/Q samples/s (sufficient for narrowband WSPR, not APRS).
- Groups of 15 complex samples as signed I/Q bytes, a per-row scale shift,
  and a 96 MHz timestamp for the final sample. Each line is 53 characters,
  roughly 13 lines/s, encoded as base64 text.
- Host reconstruction uses timestamps, fills gaps for lost lines, fits the
  effective sample rate and resamples before decoding. DMA throughput can
  change by a few percent with CPU load.
- Delays UI work and status/serial output outside the tight DMA drain loop;
  exposes loss counts and idle statistics.

For APRS, preserve the architecture but use a **much wider baseband**. Bell
202's 1200 and 2200 Hz tones require sufficient output sample rate and
anti-alias filtering; 199 complex samples/s is inadequate. A proposed first
prototype would mix to complex baseband and use a properly characterized CIC
chain to produce ~9.6 k complex samples/s, or recover the audio discriminator
at an appropriate rate, then use a binary transport. At 9.6 k complex
samples/s with 8-bit I and Q, payload is 19.2 KB/s, requiring at least
192 kbaud at 8N1 before framing; 230400 might work but needs hardware proof.
A one-bit demodulated/audio-derived stream could be much smaller, but must
retain the information needed to recover Bell 202 under real impairments.

Do **not** reuse WSPR's decimation constants or claim it preserves APRS;
validate anti-alias response, IF center, FM deviation, timestamp continuity,
DMA overruns, and serial throughput with hardware captures. In particular,
previous `gp0dma` measurements show the timer does not pace DMA as expected.

The browser workbench now exposes **direct** and **measured-rate held-GPIO**
DMA modes for both synthetic APRS and imported IF logs. The measured mode
models multiple DMA reads of the same GPIO level; it does not imply calibrated
RF sampling, bus arbitration, or verified decode. Default remains direct.

## General-purpose IQ stream protocol (host prototype)

`sim/k6emu/iq_stream.py` provides a first transport-independent binary IQ
framing prototype: magic `K6IQ`, version, flags, sample count, sequence,
96 MHz end-of-frame timestamp, explicit output rate, interleaved signed
8-bit I/Q, and CRC32. The simulator can packetize arbitrary IQ samples
with exact integer timestamp arithmetic. The receiver detects missing or
out-of-order frames rather than silently claiming continuity. This is
intentionally **not APRS-specific**: a PC can archive/replay the same frames
for other narrowband digital modes and experiments.

The format is currently a **host/simulator fixture only**. It does not yet
run on the K6, nor does it solve the DSP and serial bandwidth constraints.
At 9.6 k complex samples/s, payload is 19,200 B/s, before the 24-byte
per-frame overhead. At 64 samples/frame, overhead adds 3,600 B/s, requiring
228 kbaud at 8N1 even without any UART gaps; 230400 baud is too close to
saturation for reliable operation. A 128-sample frame cuts framing overhead
in half, but still requires ~210 kbaud and leaves limited margin. Binary
transport or lower rates and hardware measurement remain prerequisites.

## Wideband IF-to-IQ reference pipeline

`sim/k6emu/iq_frontend.py` now implements an **offline, decoder-neutral DSP
reference** using GP0 population counts (64 DMA observations per count),
IF mixing, a sixth-order low-pass filter, rational resampling to 9.6 kHz,
int8 quantization, and the generic timestamped IQ frame format. Tests verify
that a +1200 Hz signal survives mixing and decimation, that an out-of-band
signal is attenuated, and that the framed output reconstructs with no gaps.

The Python/SciPy reference intentionally uses a convenient filter and is
**not MCU code**. It supplies a target for the next stage: implementing an
on-radio efficient multistage decimator, testing its cycle budget and alias
rejection against this reference, then measuring sustained serial delivery.
The source is modeled as idealized population counts, not a proven physical
APRS capture; RF acquisition and successful AX.25 decoding remain open.

## Portable Cortex-M0 fixed-point DSP core (first implementation)

`c/k6/if_iq.h` and `c/k6/if_iq.c` now contain a small, transport-neutral
streaming mixer. It accepts centered GP0 population counts, advances a
32-bit phase accumulator at the effective count rate, mixes using a 16-entry
Q7 cosine table, and emits signed I/Q after a configurable number of counts.
A native host test compiles the **same C code** and checks output count and
a synthetic +1200 Hz signal. The Thumb/Cortex-M0 object compiles to about
144 bytes of code plus constant data in the current build.

**This is not yet a radio module or an APRS-ready decimator.** Its simple
boxcar aggregation has inadequate anti-alias rejection for arbitrary IF
signals; a multistage CIC/FIR and scaling/overflow bounds must be designed
and validated before connecting it to GP0 DMA or exporting real captures.
The source sample clock also needs measurement and correction. Keeping the
core separate allows unit testing before exposing it to radio hardware.

## Fixed-point CIC stage

`c/k6/if_cic.[ch]` adds a portable, third-order complex CIC decimator with
power-of-two factors 1..32, modular 32-bit integrators, three comb stages,
and divide-free scaling. This is separate from the earlier mixer, allowing
filter design and benchmarking without touching the production WSPR receiver.
Host-native tests exercise positive/negative DC gain, output counts, and
suppression of a synthetic signal that would alias through a simple boxcar.
The Cortex-M0 build is 292 bytes of text at `-Oz` on this toolchain.

**Limitations:** CIC stopband nulls and passband droop depend on frequency;
the alias-rejection test is one targeted frequency, not a full filter response
qualification. An appropriate compensation FIR and multistage rate plan are
still required for a robust 9.6 kHz stream. The CIC code is compiled for M0,
but its instruction-cycle cost and integration with GP0 DMA are **not yet
measured in the emulator or on hardware**. Avoid presenting host execution
speed as MCU throughput.
