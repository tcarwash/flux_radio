# Flux FSK-09S-C/D/E/F — fixed-clock `0x00F8` leading-word forensic analysis

Date: 2026-11-23. **OFFLINE only**: independent re-analysis of the saved conducted/dummy-load SDR captures, no serial, no RF, no transmit, no firmware mutation, no hardware lease. Other research thread has resumed; this work was coordinated through shared queue #11 and isolated issue #15. Parent issue #9 remains OPEN for streaming beyond actual FIFO capacity.

## Critical result: a short sync's ±0.2% baud choice skewed FSK-09S-F

The earlier general-purpose native-FSK synchronization receiver searched 4800bps ±0.2% and chose the strongest **160-bit** preamble+sync correlation (not known payload). In the failed FSK-09S-F 60-word hardware test, it selected **4790.4bps** with correlation `0.99261`, a slightly better public-sync match than exact 4800bps `0.992576`. That difference accrues almost **two bits of clock drift over a 960-bit frame**, making a one-word-displaced frame look further corrupted. The user has already verified that the K6's *programmed* native FSK modem baud for these tests is **exactly 4800bps**, so the correction for controlled laboratory forensic decoding is to lock the receiver to **4800bps**, use only public `16×0x55` preamble and `07 6B 3E 36` sync for timing/polarity, and compare expected data **only after independent symbol decisions**.

Under the new forensic decoder, the previous **60-word F result** improves from **363** nominal bit errors (4790.4bps acquisition) to **281** nominal errors at 4800bps, but still **fails** its precommitted strict full-frame test. The material result is that **after skipping the unexpected first word, all subsequent 59 intended words (944 bits) match the expected data with zero errors**, including **11 of the 12 words appended after transmission began**. The last intended word was displaced beyond the programmed 60-word frame. This new realignment was found **after the physical experiment** and is **diagnostic, not a retroactive pass**. It is nonetheless direct evidence of RF output incorporating several mid-TX writes.

## Cross-capture, fixed-4800bps results

The read-only script `tools/k6_fsk09s_forensic_4800.py` processed FOUR original IQ files, not newly transmitted frames. It allows **only the configured rate factor 1.0**, and uses only the universal PHY preamble/sync for acquisition. All expected payload truth is held until *after* RF decoding. The original prospective score files and source captures remain untouched. Output local JSON: `/home/tyler/flux-bench/tx-injection-20261122/fsk09s-forensic-fixed4800-20261123.json`.

| Experiment/frame | Initial FIFO / subsequent live writes | Strict nominal RF payload errors | First observed word (LE bytes) | Post-hoc +1-word shift: remaining intended words | Formal outcome |
| --- | --- | ---: | --- | --- | --- |
| **C control** | 49 preloaded / none | **0/784** | `5555` | shift not needed | PASS control |
| **C second frame** | 48 preloaded / 1 live | **191/784** | `f800` (=0x00F8) | **48/48 exact** (768 bits) | FAIL |
| **D single frame** | 48 preloaded / 1 live | **0/784** | `aaaa` | shift not needed; final appended `0xA53C` exact | **PASS** |
| **E single frame** | 48 preloaded / 4×4 live | **316/1024** | `f800` | **63/63 exact** (1008 bits), including 15 live-written | FAIL |
| **F single frame** | 48 preloaded / 3×4 live | **281/960** | `f800` | **59/59 exact** (944 bits), including 11 live-written | FAIL |

All four cases use frozen bench data from independent tests, whose original IQ SHA-256 digests are: C `7981d591aa03d028f99c6db89e33fa0e13964acf881251951ead62bb2f547ff4`; D `fa31e32931ade4f10d3d1af702bd32590cb94ff75da3a86d11ae1e85ffc55e00`; E `9ca7fdfb3c5ce08b103c4c45295a23744aec94021e078dcb870e9fc0b2084825`; F `2334ad4ccd37447263f5e0542292629d00d2ff094efc682205ae292a6e4df2b6`.

**Interpretation:** Across C-second, E and F the exact **same unexpected first 16-bit `0x00F8`** physically decoded before the otherwise correctly ordered FIFO payload. Every test formally failed whenever this appeared because it displaced the last word beyond the declared finite-frame length. D and C-control did not emit that leading word and both decoded bit-perfect. There is a repeatable *frame initialization/first-word anomaly* correlated with some, but not all, TX sequences or append strategies. This is **not evidence of random bit corruption throughout the appended data**, and **not proof** that the FD6818B transmitter can already stream indefinitely.

## Offline source audit and remaining unknown

- `firmware/k6-aura/aura-pager.patch` defines **`CTRL_BASE = 0x00F8`** for the native modem (`REG_59`), exactly matching the anomalous RF first word. The lab C source in each case writes the **same 0x00F8 value** during FSK configuration/control/FIFO clearing before starting FSK TX. This is a **strong lead**, but the correlation is **not yet causal proof**. A stale control word, SPI shift/register misaddress, modem reset/clear sequencing and receiver mapping artifacts remain possibilities.
- The source-visible `mod_fd_write()` routes directly through the synchronous `app.radio.fd6818.reg_write(syst,reg,value)` call after the TX guard, with no obvious application-level queue. The lower-level hardware SPI transaction implementation needs an independent, on-device build-specific audit before asserting any root cause.
- The physical module's `REG_59` normal sequence is: `0x00F8` (base), `0x80F8` (clear FIFO), `0x00F8` again, followed by all preloaded words written to `REG_5F`, then `0x08F8` (start). Both E/F contain this, while D works, so changing register ordering blindly is **not** justified.
- Programmed frame lengths differ across C/D/E/F and the RF layout contains a word-boundary anomaly. The 4800bps receiver may still require clock recovery for real operating channels, but in this controlled lab the ±0.2% candidate selection is demonstrably risky when only 160 known framing bits are available and **no CRC** protects the test vector.

## Next test proposal (NOT approved for RF by this report)

First use offline virtual timing/ABI review to isolate which action can inject 0x00F8; then compare a **new fully preloaded 60-word control** against a **new single-frame 48+2 (or 48+1 with noninverted test bits)** at strictly frozen TX settings. This would distinguish the `REG_59`/frame-length sequencing artifact from a batch-write timing effect while keeping at most 60/64 known-safe writes and respecting independently proven signed-slot/VFO restoration. Any live experiment must be **separately prospectively committed**, acknowledged via shared queue #11, acquire the canonical nonblocking Flux serial lock, use dummy load and bounded TX lease, preserve raw IQ and reject missing appended tail words as failures.

Do not attempt >64-word FIFO streaming, spontaneous tone playbacks or firmware flashes while the other RF agent owns the bench. The **one-word in-flight success (D)** is a physical finding; **repeatable multiword clean framing and FIFO beyond capacity** remain unresolved.

### Reproduce offline

```bash
python3 tools/k6_fsk09s_forensic_4800.py \
  --out /tmp/k6-fsk09s-fixed4800-independent.json
python3 -m pytest -q tests/test_k6_fsk09s_forensic_4800.py
```
