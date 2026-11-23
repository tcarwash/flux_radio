# FSK-09S-A — FD6818B native-FSK TX source audit

**Status:** source-level audit only; no RF, SPI, register write, or FIFO access
was performed for this note. It narrows the next test and does not establish
the B-chip FIFO's undocumented TX occupancy or interrupt semantics.

## What the current TX path actually does

The generated FD6818B module API in
`firmware/k6-aura/apply-configurable-fsk.py` configures one finite frame by
writing mode, fixed framing/sync fields, baud, gain, and frame length; it clears
the FIFO, writes all requested FIFO words, enables TX, then polls `REG_0C` bit
0 for completion. The module is bounded to 128 words. It has no mid-frame
refill callback, occupancy read, or status-returned free-space count. The
fixed-frame driver patch in `firmware/k6-aura/apply-fd6818b-fsk-tx-api.py`
likewise passes exactly eight words to the B driver, waits in 5-ms intervals
on the same completion bit, and clears work/interrupt state on exit. These are
facts about Flux's current driver path, not proof that silicon lacks another
TX mode.

The FD6818B pager patch's RX IRQ helper first checks `REG_0C & 1`, then writes
zero to `REG_02` and reads `REG_02` to obtain pending causes. Its comment warns
that reading before this clear/latch step can return the previous interrupt's
causes. A TX sampler may observe `REG_02` without writing it, but must treat
values as possibly stale and must not infer “no event” from an unchanged read.
The physical FSK-09 telemetry saw `REG_0C=0x0220` and `REG_02=0x0800`
throughout its bounded refill wait; this confirms those observations did not
expose the assumed almost-empty bit, but does not validate `REG_02` as a
complete B-chip FIFO map.

The abandoned experimental refill source
`c/k6/mod/fsk_refill.c` writes `REG_5E` using the **older FD6818/BK4819**
almost-empty threshold interpretation, gates append operations on the
**older-family** `REG_3F`/`REG_02` flag assumption, and stops without writing
more words when that flag fails to arrive. The physical FSK-09 report records
two bounded no-IRQ aborts and safely preserved the 48 preloaded words. It
explicitly does not validate the old threshold map on 0x4829. The source's
existing safe invariant is important: it never reads destructive `REG_5F` to
guess FIFO headroom.

`c/k6/mod/fsk_fifo_probe.c` performs read-only snapshots but intentionally
skips `REG_5F`; its idle snapshot cannot describe TX occupancy. The audit
found no tracked B-specific API that reports FIFO fill/free words during TX,
and no verified B-specific almost-empty or refill interrupt mapping. The
directly useful completion candidate supported by the TX code is `REG_0C & 1`;
it marks frame completion, not remaining headroom.

## Physical status-only result

The frozen [FSK-09S-B observation](k6-fsk09s-status-probe-physical-20261123.md)
ran twice with fully preloaded 16/32/48-word frames. `REG_5E=0x3044`,
`REG_3F=0x8000`, and `REG_30=0xC1FA` stayed fixed. `REG_0C` only asserted
completion (`0x0221`) after 87/140/193 ms; that is terminal status, not
headroom. `REG_02` was `0x0800` only in run 01's first frame, then `0x8000`;
run 02 read `0x8000` throughout. This did not repeat or track remaining FIFO
words, so the predeclared status-signal criterion failed. No FIFO append was
attempted.

## Next safe discriminator

Do not repeat the same old-family almost-empty IRQ guess. Before another
refill attempt, find a B-specific source or interface description for an
unframed/transparent TX mode or another documented occupancy signal. Any new
candidate must first receive a fresh source/read-side-effect audit, then a
frozen test, and must change repeatably with remaining payload length across
independent captures. Never write an uncertain threshold or append FIFO words
without a proven capacity bound.

This source result supports an experiment design only. The measured 46.719-ms
inter-frame nonpayload interval still prevents claiming transparent 4,800-bps
streaming, and this audit says nothing about receiver decoding, BER, spectrum,
or arbitrary complex-IQ output.
