# Flux FSK-09S-C — first one-word timed FIFO append test: NOT proven

Date: 2026-11-23. User approved exclusive K6 bench use; physically conducted dummy-load test, air-coupled SDR. Prospective exact test source and pass criteria frozen in [commit 1eb2685](https://github.com/tcarwash/flux_radio/commit/1eb2685), before transmission. A source-level status-only probe from the other RF research agent had previously failed to identify an almost-empty/occupancy signal. This test **does not depend on any status/IRQ interpretation**: maximum writes since FIFO clear are **49 words**, below the previously proven 64-word preload capacity. Both frames used a single bounded 1900ms lease; no other agent's worktree modified.

## Raw result

The 49-word **fully preloaded control frame** and the **48-word-preload + one scheduled live append** frame were transmitted in one radio-module invocation. Firmware reported result 98 (49+49) with no errors, but this cannot establish transmitted payload accuracy. Trusted original slot30 image was backed up and restored/readback verified; both VFOs at 144.390MHz, final TX idle. The canonical Flux serial flock was held through the full RF capture and restoration.

An initial *known-payload training* decoder incorrectly locked both test patterns to the same 0xAAAA periodic portion and produced an unusable result. This ambiguity was diagnosed **after the RF test**. We changed the offline decoder to lock **only to the public native 16×0x55 preamble + `07 6B 3E 36` sync**, then demodulate 98 bytes without looking at either expected payload. All expected-data comparisons below are post-hoc. The revised decoder itself was not frozen before the RF test; retain this caveat when interpreting the negative result.

| Metric | Fully preloaded control | Timed one-word append frame |
| --- | --- | --- |
| Modem programmed length | 49 words (98B) | 49 words (98B) |
| Before-TX FIFO writes | 49 | 48 |
| Additional FIFO writes after TX start | 0 | **1 at ≥70ms** |
| Independent frame acquisition/sync | PASS | PASS |
| Full decoded 784 payload bits | **0 errors** | **191 errors** at nominal start |
| Last recovered 16-bit word, little-endian hex | `a53c` (0x3CA5) | `3cd3` (0xD33C) |
| Expected last word, little-endian hex | `a53c` | `3ca5` (0xA53C) |
| CRC/known-tail proof of append | control only | **FAIL** |

The observed frame-start separation was **205.625ms** (frames are individually rearmed). The second frame's *first observed 16-bit word* is unexpectedly `0x00F8` (received LE bytes `f800`). When this first word is skipped, the subsequent **48 words exactly match all 48 preloaded words, zero errors**. The distinct last marker written in-flight is **absent** from that declared 49-word frame. This is consistent with an extra leading FIFO word or rearm/alignment artifact consuming one frame word, *not* proof that the chip always rejects mid-TX append. The `0x00F8` value is also used in control-register configuration, but its source is **not established** by this test.

**Strict prospective outcome: FAILURE/INCONCLUSIVE for in-frame refill.** The module's successful return is not sufficient, and the actual RF payload did not meet the required last-word match. Do not claim gapless streaming or a successful append.

## Saved physical evidence

Local fixture directory `/home/tyler/flux-bench/tx-injection-20261122/fsk09s-oneword/`:

- `fsk09s-oneword-run01.npy`: original recorded IQ (hash in companion analysis JSON).
- `fsk09s-oneword-run01.json`: original radio/SDR transaction, trusted signed slot readback and VFO restoration.
- `fsk09s-oneword-analysis-run01.json`: the **invalid ambiguous-prefix** first analysis, preserved for transparency.
- `fsk09s-oneword-sync-analysis-run01.json`: public-sync-only independent-payload reanalysis described above.

Reproduce without radio:

```bash
python3 tools/k6_fsk_oneword_analyze.py \
 --iq /home/tyler/flux-bench/tx-injection-20261122/fsk09s-oneword/fsk09s-oneword-run01.npy \
 --out /tmp/fsk09s-oneword-public-sync-reanalysis.json
```

## Next separated discriminator

To determine whether the **unusual first word belongs to rearm rather than append**, a *new*, **single-frame** prospective experiment can transmit 48 known words followed by one timed word (one nominal 49-word frame). Its absence of prior frame rearm removes a confound; it still caps total writes at 49 and remains fully guarded. Freeze the separate source and acceptance criteria *before* any physical attempt; no automatic retry of this experiment. A negative single-frame test does not exclude a mode-dependent streaming capability elsewhere in the RFIC.
