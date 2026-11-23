# FSK-09S-B — status-only 16/32/48-word observation, two RF captures

The exact probe image, sample schedule, receiver-status interpretation, and
stop rule were frozen in commit `cf592bf` before either run. Both guarded
transmissions used one 1,800-ms lease, preloaded each frame fully, never
appended FIFO words, never read `REG_5F`, and never wrote `REG_5E`. The radio
reported 96/96 total words complete in each run; the original slot-30 module
and both 144.390-MHz VFOs were restored and verified.

Exact module code SHA-256:
`2186deaa27b689c81651ac8bfb914fb6e647a83d12e7d62d05044a13a4991823`.

| Run | IQ SHA-256 | Captured RF activity | Frame completion (`REG_0C`) | `REG_5E` / `REG_3F` / `REG_30` |
|---|---|---|---|---|
| 01 | `8a753dfb43b30e0d6c6bf5e2678f0885ffdb23e35d49c2a205e329c6ad491bfc` | 10 active 50-ms windows, 0.75–1.20 s in the saved 5.12-s IQ | `0x0221` at 87 / 140 / 193 ms for 16 / 32 / 48 words; `0x0220` before completion | `0x3044` / `0x8000` / `0xC1FA`, unchanged throughout |
| 02 | `bbd226a36cbcecabb7a9cac00acdc9444e8e507cb535a10532db98c01fc2ba9d` | 10 active 50-ms windows, 0.75–1.20 s in the saved 5.12-s IQ | `0x0221` at 87 / 140 / 193 ms for 16 / 32 / 48 words; `0x0220` before completion | `0x3044` / `0x8000` / `0xC1FA`, unchanged throughout |

`REG_02` stayed `0x0800` for run 01's first 16-word frame, then `0x8000`
for the 32- and 48-word frames; run 02 read `0x8000` throughout. The
boundary-dependent difference did not repeat, and neither value tracked
remaining FIFO words. It is not a refill signal. `REG_0C & 1` changed only
at frame completion, and therefore reports that a frame has ended rather
than available FIFO capacity. `REG_5E`, work mode, and the state word showed
no occupancy-dependent changes. This fails the predeclared headroom-signal
criterion.

The offline RF-activity analyzer confirms a bounded burst was captured, but
explicitly does not identify FSK or decode data. Its uncalibrated SDR reports
cannot establish RF power, frequency accuracy, or emission compliance. No
blind refill was attempted. The B-chip's current API remains fixed-frame;
the next FSK-streaming work should move to a B-specific source/ABI audit for
transparent/unframed modes or focus on packetized, independently decoded
frames and jitter-buffered voice.

Raw IQ, activity JSON, transaction receipts, and signed original-slot backups
are preserved outside Git under
`/home/tyler/flux-bench/tx-injection-20261122/frame-refill/` with
`fsk09s-status-run01/02` names.
