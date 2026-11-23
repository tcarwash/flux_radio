# Flux FSK-09S-E — four in-flight FIFO batches, physical partial result

Date: 2026-11-23. **Predeclared full 64-word success criterion: FAILED.** Separate, meaningful observation: **15 of 16 words written during TX were subsequently decoded in order over RF**, after accounting for an unexpected leading word. Freeze before hardware: [`bb820e7`](https://github.com/tcarwash/flux_radio/commit/bb820e7), [FSK-09S-E protocol](k6-fsk09s-fourbatch-precommitted-20261123.md). Do not reclassify this run as a formal pass.

## Test and safety

One standalone native-FSK frame at 4,800bps, `REG_5D=0x7f00` (64 words/128 bytes). Preloaded 48 known words, then four scheduled **4-word batches** were added after TX start at >=70, 95, 120 and 145ms. **64 total words** is no more than the same radio's verified initial 64-word preload capacity, so we needed no unproven occupancy, AE interrupt or destructive FIFO read. Radio reported result 64. Original signed slot30 image and both RX VFOs restored and independently verified, TX idle, no additional RF test/retry. The canonical nonblocking Flux serial lock was owned for entire guarded radio/SDR/restoration transaction. Fixture: user-confirmed dummy-loaded 433.500MHz K6 FD6818B 0x4829, air-coupled RTL-SDR at 433.750MHz 1.024Msps, 1900ms TX watchdog.

## SDR evidence and precommitted outcome

The public-preamble/sync receiver acquired **one** native-FSK frame with 0.994645 correlation, with no expected payload data used for synchronization. The decoded **128 bytes** do **not** match the frozen source at their nominal bit positions: **316/1024 payload bit errors**, including **128/256 errors in the nominal 16 appended-word tail**. The declared full-payload test thus **did not pass**.

However, the decoded payload starts with an unexpected little-endian `f8 00` (**0x00F8**) 16-bit word, followed by **exactly the expected first 63 words**, with **0 errors over those 1008 bits** after a one-word post-hoc shift. That includes **all 48 original preloaded words plus the first 15 words from the four after-start batches**, which could not have been present in the original 48-word preload. The last scheduled (64th) appended word was **not recovered within the hardware-declared 64-word frame**. Reading beyond the declared frame shows no reliable matching tail, so do not credit the last word.

This pattern is consistent with a one-word leading artifact displacing the final FIFO word. A similar extra `0x00F8` leading word occurred in the failed *second frame* of FSK-09S-C, while a separate 49-word *single-frame* FSK-09S-D successfully sent its appended final word with zero errors. **The source/cause of `0x00F8` is not established**, and the +1-word alignment was noticed **after** RF, not predeclared as success. This evidence favors the possibility that multiple in-flight writes are supported despite a boundary/rearm/configuration artifact, but it does not qualify high-confidence streaming or proper 64-word output.

## Evidence

- Original SDR IQ: `/home/tyler/flux-bench/tx-injection-20261122/fsk09s-fourbatch/fsk09s-fourbatch-run01.npy`, SHA256 `9ca7fdfb3c5ce08b103c4c45295a23744aec94021e078dcb870e9fc0b2084825`.
- Original bench/restoration telemetry: `fsk09s-fourbatch-run01.json`.
- Original strict analyzer: `fsk09s-fourbatch-analysis-run01.json`.
- Post-hoc shift-specific diagnostic: `fsk09s-fourbatch-posthoc-shift-run01.json`; `one_word_shift_first63_bit_errors=0`, `shifted_first15_appended_words_exact=true`. `tools/k6_fsk_fourbatch_analyze.py` retains **FAIL** as its result; it merely adds forensic diagnostics.
- Source SHA256 `79f545d65f6006a34d511679d644429c383cb1bef4cc7970729fce7cb320ed8e`; compiled image SHA256 `a75d0bf0330ffda460c13ef30165ecad2fe02bf9de8a3412b55be51c4cd074c4`.

## Next safe discriminator

Use a separate **60-word** frame, 48 preloaded then **three 4-word batches**, keeping **four words below the verified 64-word FIFO capacity**; require strict exact 960-bit SDR recovery. Avoid >64 total writes until reliable capacity/headroom can be independently established, or the repeatable `0x00F8` framing artifact is understood. The earlier **one-word successful** in-flight append remains the strongest unambiguous demonstration. An eight/12-word multi-append success would materially reinforce it; even that would not yet be gapless >64-word streaming.
