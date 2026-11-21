# Bell 202 soft metrics at a measured PA11 sample rate

tools/k6_bell202_soft.py is an offline, **reference-assisted** Bell 202
demodulator. It accepts mono 16-bit WAV at an arbitrary sample rate, including
the measured 17.5 kHz PA11 recordings, without rounding the rate to an integer
number of samples per bit. Mark is 1200 Hz, space 2200 Hz, baud 1200.

The tool uses two quadrature tone-energy integrals and searches for the
beginning of a known synchronization word in sample units. It can also test
common tone offsets of -100, -50, 0, +50 and +100 Hz. It outputs per-symbol
signed soft scores and hard decisions. A training word, supplied as known
bits, establishes alignment before decoding the remaining symbols.

**Not an AX.25 decoder:** the supplied bit list is a reference, not the
radio extracting arbitrary callsigns, NRZI, HDLC, FCS, or real packets.
There is no on-radio decoder in this stage. The sync score is an energy
correlation measure, not a calibrated log-likelihood or channel SNR.
Without a known transmitted bit sequence the measured BER is undefined.

## Reproduce

The existing bench emits a deterministic clean 48 kHz waveform and metadata:

~~~sh
python3 tools/k6_afsk_audio_bench.py gen \
  --out-dir /tmp/flux-afsk-reference --n-symbols 1200 --seed 17

python3 tools/k6_bell202_soft.py \
  /tmp/flux-afsk-reference/afsk-bench.wav \
  --meta /tmp/flux-afsk-reference/afsk-bench.meta.json \
  --sync-len 64
~~~

The reference decoder also passes synthetic tests at 17,504 and 17,534 Hz,
including an unknown leading-sample offset and a +50 Hz tone shift. In one
deterministic 256-symbol test, 0 dB additive white noise gave approximately
4% bit errors after alignment; this is synthetic baseband, not a measured
RF sensitivity or packet success rate. The decoder rejects a noise-only
test without an adequate known-sync correlation.

## Next physical experiment

Transmit a known short 1200/2200 Hz Bell 202 training sequence through a
second lawful/authorized RF source into the receiving Flux radio. Capture
PA11 samples and compare them with the separately established GP0/IQ path,
using the same RF stimulus and actual sample timebase. Because the current
PA11 capture is only approximately 58 ms, begin with a very short sequence
(under 65 raw symbols) and a known training prefix; it is not yet long
enough for normal AX.25 frames. This short-capture reference tool is a
step toward synchronization, not a standalone on-air modem.

Before attempting 1–2 second radio packets, implement a bounded real-time
capture/demodulation pipeline and validate CPU, interrupt latency,
overruns, frequency offset, packet CRC and FEC. Do not interpret the
positive short-burst tests as a deployable Packet/APRS receiver.
