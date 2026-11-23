# FSK-09S-B — predeclared status-only observation

**Protocol frozen before RF.** This test asks whether any non-FIFO register
read exposes a repeatable state change that tracks native-FSK frame length.
It does not refill the FIFO and cannot qualify continuous streaming.

## One bounded transmission

The hash-pinned module transmits three independently framed payloads of 16,
32, and 48 FIFO words (all below the previously used 64-word capacity), in
that order under one 1,800-ms TX lease. It preloads every frame completely;
the 48-word test frame is never appended to. The PA stays on while the modem
is rearmed between frames. Each frame uses the established 4,800-bps B-chip
register setup and known prefix of the existing test vector.

During each active frame, the module samples `REG_02`, `REG_0C`, `REG_3F`,
`REG_5E`, and `REG_30` at start, quarter points of the nominal payload
duration, and the observed completion point (at most six observations per
frame). It does not write these registers during the active frame. In
particular, `REG_02` is sampled without its clear/latch write, so its values
may be stale; no absence-of-event inference will be made from it. `REG_0C & 1`
is treated only as frame-completion status. `REG_5E` remains read-only because
its B-chip meaning is unknown.

The only writes to `REG_02` are the existing pre-frame clear operations. The
module never reads `REG_5F`, never changes `REG_5E`, and never writes more
than the declared frame length. It preserves/restores the existing FSK
register baseline, refuses the wrong chip/channel or busy radio, checks
operator key-up and the hardware power switch throughout, and releases TX on
all exits. The one-shot host transaction requires the explicit dummy-load
acknowledgement, known firmware, SDR baseline, full slot-30 backup, and
verified module/VFO restoration. It does not flash MCU firmware or erase
calibration/SPI.

## Frozen interpretation

Only a repeatable register transition that tracks the 16/32/48-word frame
length and is distinct from the terminal `REG_0C` completion event will be
considered a candidate occupancy signal. A value that stays constant,
appears only at frame end, or differs once without length ordering is **not**
FIFO headroom. No threshold or IRQ-gate writes and no refill will be based on
this experiment alone. Any promising signal requires independent repeats and
a separate capacity proof before one small append trial.

## Frozen artifacts

Source: `c/k6/mod/fsk_status_probe.c`; RF wrapper:
`c/k6/mod/fsk_status_probe_rf.c`; module code SHA-256:
`2186deaa27b689c81651ac8bfb914fb6e647a83d12e7d62d05044a13a4991823`.
Host one-shot: `tools/k6_fsk_status_rf_bench.py`. Tests:
`tests/test_k6_fsk_status_probe.py`.

Raw SDR IQ and slot backup will use fresh `fsk09s-status-runNN` archive names
under `/home/tyler/flux-bench/tx-injection-20261122/frame-refill/`; no prior
capture or backup is overwritten.
