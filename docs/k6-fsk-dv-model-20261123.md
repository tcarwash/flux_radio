# FSK-10A native-FSK packet capacity model

Date: 2026-11-23. Status: **offline geometry and deadline simulation only**.
This is a companion to the [FSK streaming/DV roadmap](k6-fsk-streaming-packet-dv-roadmap-20261123.md),
not a physical packet qualification.

## Model

`tools/k6_fsk_dv_model.py` uses the existing 64-word / 1,024-bit native FIFO,
4,800-bit/s symbol rate, and the measured 46.719-ms nonpayload interval between
the two FSK-09 test frames. For each assumed Sotto packet size it accounts for
a four-byte sequence/length/codec header, two-byte CRC, optional idealized
rate-1/2 bit repetition, payload transmit time, and one observed restart gap.
The deterministic deadline simulation injects packet erasures and bounded
arrival-time jitter against a fixed-delay playout schedule.

This deliberately leaves the physical packet format and FEC undecided. The
model treats payload bytes as capacity; it does not encode or inspect speech,
simulate an RF waveform, decode packets, model PLC, or prove the 46.719-ms gap
for every transition.

## Selected geometry

Eight Sotto-1200 frames represent 320 ms of compressed speech and 48 bytes of
payload. With the proposed four-byte header, CRC-16, and idealized repetition,
the transmitted packet is 108 bytes (864 bits), which fits the 1,024-bit FIFO.
At 4,800 bits/s plus 46.719 ms restart, packet time is 226.719 ms and modeled
application goodput is 1,693.73 bit/s, above the codec's 1,200 bit/s input
rate. This supports testing packetized voice on the finite-frame bearer; it
does not prove stable live playback.

For 500 modeled packets at 5% independent erasure probability, ±150 ms jitter,
and a 600-ms fixed playout delay, seed 20261123 produced 31 erased packets and
31 missed packet deadlines, or 9.92 seconds of audio requiring concealment.
No packets were late in this seeded run. Results vary with loss/jitter seed and
are only a planning reference.

## Validation and next evidence

`pytest -q tests/test_k6_fsk_dv_model.py`: 4 passed. The tests check the
observed two-frame gross-rate arithmetic, FIFO fit, determinism, erasure
accounting, and rejection of an oversized protected frame.

To complete 10-A, feed actual prerecorded Sotto 1200/600/400 compressed frames
through an explicit packet encoder/decoder, sweep loss and jitter across
predeclared seeds, and measure buffer occupancy, interruption and latency.
Then freeze a pilot-only independently trained IQ receiver and test new
payload data in two-to-four bounded conducted RF frames before making any
bearer or real-time voice claim.
