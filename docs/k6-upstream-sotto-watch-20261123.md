# Flux upstream watch: Sotto 1200 v3.10 and Blowfang current baseline

**Date:** 2026-11-23. **Scope:** source-pinned read-only upstream audit and *offline* codec/packet compatibility smoke checks. No radio, SDR, serial, flash, hardware lease, or changes to other worktrees.

## Upstream revisions

- Sotto: [`baitisj/sotto@e707134`](https://github.com/baitisj/sotto/commit/e707134fd7f9e2721b55b9d983b77a8aa5593552) (`main` when checked), compared to the Flux codec checkout's cached [`598f1bc`](https://github.com/baitisj/sotto/commit/598f1bce87c22ebada2b9d0d1fcfd89107b33a09). Verified with `git ls-remote` and an **isolated bare source checkout** at `/home/tyler/flux-bench/upstream-watch-20261123/sotto.git`. No change to the active detached Sotto checkout.
- Blowfang: [`baitisj/blowfang@d580eb0`](https://github.com/baitisj/blowfang/commit/d580eb067352191a69c75fa355890de89c9c136f), exactly the same SHA as Flux's already-cached `upstream/main` at audit time. **No new Blowfang `main` commits beyond the other agent's existing** [upstream reconciliation](https://github.com/tcarwash/flux_radio/blob/research/upstream-blowfang-audit-20261123/docs/k6-upstream-blowfang-reconciliation-20261123.md); do not duplicate its issue comments. Upstream's physical original-0x4819 `micsotto.c` evidence is already posted in Flux [#10](https://github.com/tcarwash/flux_radio/issues/10#issuecomment-7117188083) and [#12](https://github.com/tcarwash/flux_radio/issues/12#issuecomment-7117188751).

## Sotto source changes relevant to Flux DV

New upstream reference-encoder pitch analysis changes in `c/fixed/sotto_fx_enc.c` and `c/sotto.c` (v3.10): low-frequency energy ranking, time-domain sub-octave penalties, odd-harmonic exemption, and creak pitch hold. See [upstream pitch error report](https://github.com/baitisj/sotto/blob/e707134fd7f9e2721b55b9d983b77a8aa5593552/docs/pitch-errors.md) and source commits [`602f54f`](https://github.com/baitisj/sotto/commit/602f54f), [`15a07e8`](https://github.com/baitisj/sotto/commit/15a07e8), [`51035e1`](https://github.com/baitisj/sotto/commit/51035e1). Upstream reports improved pitch tracking on several clips, but **we have not independently measured audio quality**.

**Crucial distinction:** these changes are **not** to the MCU's `slim_encode10` encoder. The Git blob IDs for `c/fixed/sotto_fx_slim.c` (`9f6aca31f7bee06c1e7fd6d633c9f6520b2122b7`), `c/fixed/sotto_fx_dec.c` (`a8ec9b59df415877f4f0153bea75a8d71e40ce10`) and `c/fixed/fx_tables.c` (`07ec8f84b4f1d755dfed64f948f5616e060ae7e3`) are **identical** between `598f1bc` and `e707134`. Therefore **do not silently replace Flux's pinned slim encoder test vectors with the new reference encoder's golden bitstreams**: the algorithms are not identical, even though the 6-byte/40-ms wire format remains usable. There is no new MCU code-size/CPU-speed claim here.

### Independent offline compatibility checks

Downloaded upstream *old and new* `tests/golden/{female_libri198_14s,male_libri3436_15s}__1200.bit` using `git show` into private bench storage. For each of four bitstreams, decoded with Flux's already-built, older pinned `sotto_dec_fx -1200`: **150 6-byte Sotto superframes in, 96,000B of 8-kHz 16-bit PCM out (6.0 s)**, process success, nonzero PCM RMS. No new decoder build required. This is a decoding smoke check, not a subjective or PESQ quality evaluation.

For each stream, packed via existing `modem.pager.profile('L','1200',depth=1,parity=False,short_last=True)` and reconstructed through `modem.pager.Assembler`. **19 CRC-valid L64 packets per stream, 0 CRC rejections, 150/150 good Sotto frames, compressed bytes exact** for all four streams. Specifically, the **two new upstream streams** passed **300/300 frames** and **38/38 packets**. This is host-only serialization, **not RF reception or proof of B-chip real-time encoding**.

| Voice | Sotto golden revision | 1200 bitstream SHA256 | Frames | Decoder PCM | L64 packets / reconstructed frames |
| --- | --- | --- | ---: | ---: | --- |
| female_libri198 | old `598f1bc` | `78d28b2035cad875a5ce340a423babd29344a5f515a08def17ede144551c94d6` | 150 | 96,000B | 19 / 150 exact |
| female_libri198 | new `e707134` | `c4bf296889077f3cea0cb626e6d57f6ecfea61747c2c984747c428305c400735` | 150 | 96,000B | 19 / 150 exact |
| male libri3436 | old `598f1bc` | `929527722c9608cce670` (prefix) | 150 | 96,000B | 19 / 150 exact |
| male libri3436 | new `e707134` | `9d28443c053fcc1d42e4` (prefix) | 150 | 96,000B | 19 / 150 exact |

## Actionable issue queue implications

- **[#10 live DV Sotto transport](https://github.com/tcarwash/flux_radio/issues/10):** codec wire-format and Flux L64 host-packet compatibility are preserved for these two newer reference-encoder goldens. Existing pinned slim encoder remains the correct MCU implementation for now. Keep old vs new reference goldens separate, record exact revision and SHA for every future RF or perceptual comparison.
- **[#12 PA11 microphone](https://github.com/tcarwash/flux_radio/issues/12):** no new Sotto slim MCU implementation to port; original K6 physical TIM17/W48 evidence already documented and still requires FD6818B verification. No new physical claim.
- **[#11 shared queue](https://github.com/tcarwash/flux_radio/issues/11):** Sotto source revision advanced, Blowfang `main` is current; no new bench claim or RF experiment warranted by this upstream delta alone.

**Future watch:** compare only upstream commits since the last recorded SHAs above, review source changes and test evidence before posting to the specific *existing* issue, deduplicate against existing comments, and avoid treating original 0x4819 hardware measurements as proven 0x4829 results. Do not merge upstream or flash the K6 merely to update the watch.
