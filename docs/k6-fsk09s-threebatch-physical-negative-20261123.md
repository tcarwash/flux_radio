# Flux FSK-09S-F — 60-word three-batch FIFO append: negative/inconclusive

Date: 2026-11-23. Prospective exact module, decoder, pass/fail protocol and frozen hashes were committed **before** any RF as [`3b0f595`](https://github.com/tcarwash/flux_radio/commit/3b0f595), [predeclared protocol](k6-fsk09s-threebatch-precommitted-20261123.md). Single bounded physical transmission on user-confirmed dummy-loaded K6 FD6818B 0x4829 with canonical shared Flux serial lock.

## Physical facts

The module programmed **one 60-word/120-byte native 4,800-bps FSK frame**, preloaded 48 words then attempted 3 separate four-word `REG_5F` write batches at >=70, 95 and 120ms after TX start. This totals **60 words** and therefore remains four words below the same chip's **64-word physically proven preload capacity**, even if the FIFO has not drained; it is not a >64-word stream, and no unknown IRQ, REG5E threshold or destructive FIFO read was used. One hard 1900ms TX lease, short frame completion deadline.

Firmware returned result **60** and reported completion. Original trusted signed slot30 module was restored, independently read back, both VFOs restored to 144.390MHz, final TX off. One guarded RF transaction; no automatic repeat and no competing agent worktree touched.

The offline independent IQ receiver (hardware public 16×0x55 preamble plus 4-byte `07 6B 3E 36` sync, no expected data used for lock) found one packet at payload start **0.8319504s**, sync correlation **0.99261**, but decoded the **nominal 960 payload bits** with **363 bit errors**, including **96 of the 192 nominal appended-tail bits**. Its first received 16-bit word was again the anomalous **`0x00F8`** (LE `f8 00`), not the first loaded test word `0x5555`. After some leading alternating words, substantial symbol alignment/bitstream disturbance becomes evident from around word index 10, approximately where the first scheduled +4-word operation would fall given ~33ms of preamble; **causality is not established**. A post-hoc comparison of segments suggests some subsequent data resemble a one-bit rather than just one-word shift, but this is not a validated recovery and no FSK-streaming claim follows.

**Strict predeclared outcome: FAIL/INCONCLUSIVE**, not a verified multiple-batch exact stream. Contrast:

| Experiment | Physical result |
| --- | --- |
| FSK-09S-C: 49-word control then 48+1 | Control 0 errors; second frame had leading `0x00F8`, last append marker displaced, **FAIL** |
| **FSK-09S-D: single 48+1, 49 total** | **PASS, 0/784 bits errors including last appended word**, hardware rearm removed |
| FSK-09S-E: single 48+4+4+4+4, 64 total | **FAIL strict**; unexpected `0x00F8` followed by first 63 intended words, including 15/16 live-written words aligned post-hoc |
| **FSK-09S-F: single 48+4+4+4, 60 total** | **FAIL strict**, `0x00F8` first and additional bit alignment errors near first scheduled append, no full-tail proof |

The consistent surprise is the stray leading **0x00F8**, equal to a `REG_59` control value used during setup, but its true cause is **unknown**. Possible causes include B-chip FIFO reset/enable sequencing, SPI write timing/transaction effects, and a live-write-related RF symbol clock disturbance. The separate receiver initially had periodic training ambiguity in FSK-09S-C, but these E/F results use the universal hardware sync instead. Do not prematurely assert that `0x00F8` comes from a control-register write, or that in-flight appending cannot work.

## Preserved evidence

Local directory `/home/tyler/flux-bench/tx-injection-20261122/fsk09s-threebatch/`:

- `fsk09s-threebatch-run01.npy` RF IQ SHA256 `2334ad4ccd37447263f5e0542292629d00d2ff094efc682205ae292a6e4df2b6`.
- `fsk09s-threebatch-run01.json`: full hardware transaction and verified restoration record.
- `fsk09s-threebatch-analysis-run01.json`: unmodified predeclared independent sync-and-bit-error analysis; result `INCONCLUSIVE_OR_FAIL`.
- Source SHA256 `29934e5fec2ff127b5026e64e864b76b325813172bfc8d92986960bd3091f10f`, compiled 1244B module SHA256 `ac75d540231ebd071f36dde4d6f2c6108d06190de98d2b65cfd476bf573b04d5`.

## Recommended next action — offline before another transmission

**Do not proceed directly to >64-word streaming.** First audit B-specific `REG_59` reset/start effects and the ABI `fd_write` ordering, and inspect precise waveform timing around the first +4-word operation. Compare original raw IQ with the known 49-word successful single-append trace and prior 64-word full-preload captures. Prepare a new **control-only fully preloaded 60-word frame** and a separately scheduled minimal 48+2 if the audit identifies a discriminating hypothesis, and freeze both source and expected RF results before touching the radio again.

**Bottom line:** One correctly ordered post-start FIFO word is now proven. Multi-batch traffic is promising in the 64-word partial result but not established as a correctly timed and fully recovered frame. FIFO occupancy/headroom IRQ and streaming beyond 64 words remain outstanding.
