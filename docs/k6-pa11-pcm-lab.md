# Flux PA11 PCM receive lab — FD6818B (0x4829)

The new c/k6/mod/pa11pcm.c module records short RX audio bursts without
modifying RF registers, TX state, SPI, or firmware. It runs from RAM using live
serial. The host parser tools/k6_pa11_pcm.py exports a raw, completeness-checked
NPZ file, an individually normalized WAV for listening, and metadata.

## Physical measurements — November 20, 2026 (Pacific)

The initial c/k6/mod/pa11rx.c 32-sample probe succeeded on the attached radio,
but exposed a large settling transient. The PCM version discards 4,096 samples,
then records 1,024 successive samples at nominal W=90, HOLD=3. Runtime is
approximately 58.5 ms of capture plus paced report transfer.

| Radio condition | REG_47 | 1,024-sample range | Detrended RMS (counts) |
|---|---:|---:|---:|
| 146.560 MHz, idle/quiet, squelch closed | 0x6040 | 5526–5578 | 4.38 |
| 162.550 MHz, active FM reception, squelch open | 0x6140 | 5023–6271 | 141.14 |

The receiver reported approximately -72 dBm on the active frequency.
The measured 100-µs tick counts were 584 and 585, giving approximately
17,534 and 17,504 samples/s. The active signal therefore has approximately
32 times the detrended RMS of the muted baseline on this specific radio.
That is a reproducible RX AF capture result, not a calibrated SNR or a
successful digital packet decode.

The programming cable was connected in both runs. The microphone jack may
affect *microphone* measurements, but we have independently demonstrated
receive-audio sensitivity with the cable in place. The tests never keyed TX,
and normal VFO 146.560 MHz was restored afterward.

## Repeat the experiment

Requires a compatible live-serial firmware with ram_run capability and
the expanded RAM0 stack. Do not use temporary native modules on an unknown
image or unconfirmed hardware revision. From this research branch:

~~~sh
python3 tools/k6_live.py --port /dev/ttyUSB0 caps

python3 tools/k6_live.py --port /dev/ttyUSB0 run-ram \
  c/k6/mod/pa11pcm.c --cable-only --timeout 30 \
  > /tmp/flux-pa11-capture.json

python3 tools/k6_pa11_pcm.py /tmp/flux-pa11-capture.json \
  --out /tmp/flux-pa11-capture
~~~

The parser requires the successful RAM return, correct exact sample count,
monotonic row indices, complete framing, bounded CIC sample values, and
consistent min/max metadata before writing anything. The output WAV uses
the measured rounded sample rate and is **per-file gain-normalized** for
auditioning. Compare *unscaled* counts in the NPZ, not WAV amplitudes.
The measured timing is approximate: 100-µs tick resolution, clock variation,
and CPU/bus contention prevent an exact nominal rate claim.

The module saves and restores GPIOA PA11 mode/speed/output latch and the PB2
speaker amplifier. It performs no TX/PTT action, no chip register write, no
flash changes, and no host-level service mutation. It emits 86 numbered sample
rows, each limited to 54 characters, with pacing to avoid UART backlog.

## Limitations

- 58 ms is too short for a normal APRS packet, ARQ exchange, or typical
  PixelQSO burst. This validates audio capture, not a practical live modem.
- The input is *post-chip receiver audio* selected by running radio state.
  REG_47 differs between squelch-closed and squelch-open reception.
- Software-driven one-pin delta-sigma acquisition consumes substantial CPU.
  The successful foreground module does not prove sustainable simultaneous
  capture, demodulation, UX, and serial transport.
- The capture is 0x4829-specific. Upstream 0x4819 results guided the probe
  but do not imply identical RF registers or all board revisions.
- A broad compiled-image firmware emulator run had eight passes, four skips,
  and two failures (cooperative cancellation and PA15 power-off flash
  preservation). These need a separate configuration/guard audit before
  any general release. Hardware live status and RAM module runs passed.
- The SPI backup was made before the test firmware flash; no automatic
  restore or other flash operation occurs in this module or parser.

## Digital-mode next steps

1. Use a *known controlled* 1,200/2,200 Hz Bell 202 RF stimulus and capture
   matching waveforms from PA11 and the established GP0/IQ path.
2. Add symbol-timing recovery and quantify BER/PER and timing drift rather
   than scoring known boundaries only.
3. Test a bounded 600-baud 8-FSK/LDPC reference after measuring audio
   passband shape and phase/tone stability.
4. For real-time operation, replace the blocking sampler with a bounded,
   interrupt-driven pipeline and characterize CPU load, overrun reporting,
   RX continuity and mode isolation. Never infer real-time viability from
   a foreground capture.
