# Flux Sotto GP0: normalized Cortex-M0 integer division

**Date:** November 26, 2026 (Pacific). **Branch:** `optimize/sotto-codec-correlation-20261126`. **Status:** offline codec optimization and independent virtual RF qualification; **NOT installed on the radio**.

## Problem and baseline

A real dummy-load Sotto GP0 microphone test captured a `GPT1` reason-2 failure: the **4×80-sample PCM queue actually filled** during the encoder's foreground work. The optimized GP0 DMA ISR already reduced the physical peak from 4,866 to ~3,200 timer ticks, and the measured streaming SPI driver improved resource reads by 32–35%, but the actual module still returned `GP0 ENGINE` after approximately 160ms of active capture. A full physical timing capture showed one **14.8ms synchronous FSK preload** and **506 successful codec resource reads totaling ~32.7ms elapsed**. Improving preload alone delayed but did not eliminate model overflows under conservative CPU assumptions.

The Cortex-M0 codec instruction-level benchmark (400ms fixed PCM, code in RAM, no flash stalls) identified significant time in pitch analysis and software divides. The benchmark reports ~23.78MHz modeled total encoder demand and attributes ~2.21MHz to `udiv32`, ~10.03MHz to `slim_analyse10` and ~6.24MHz to `decide`. **This profile uses the upstream normalized division runtime, not the original K6 32-bit full-length divider**, and its absolute cycles do not include GP0 DMA interrupts or SPI latency.

The actual K6 loadable Sotto module in `c/k6/mod/sotto_m0rt.h` had a simpler **32-iteration, always-full-width** software division routine for the ARMv6-M processor (which has no hardware divide). The pitch-correlation work frequently uses much smaller quotients.

## Implemented change

The new compile flag **`SOTTO_FAST_DIV=1`** enables an exact normalized shift/subtract unsigned divider. Instead of scanning all 32 numerator bits, the divisor is shifted up to the numerator's range, and only the quotient's significant bits are tested. The existing zero-denominator and `n<d` behaviors remain unchanged.

This function is the common base for signed/unsigned quotient and remainder helpers, so the optimization covers multiple codec divisions without changing pitch analysis or codebook search parameters. It has no tables, allocations, new state, hardware dependencies, FPU operations, or 64-bit multiplication. The default path remains the prior 32-iteration algorithm.

The opt-in flag is exposed as `fast_div=True` in `tools/k6_sotto_module.py.build` and `--fast-div` in the offline build CLI. **The pinned upstream Sotto submodule remains unchanged.**

## Module sizing and exact-output gate

The fielded stage-2 module binary is exactly **9,784 bytes**, entry offset 16, SHA256:
`551bea3e8d72a47dcf7c97f6502b32614d7915fd4ab12d4f0b90d9f99d48fa6e`

With `SOTTO_FAST_DIV=1`:
- New code **9,772 bytes** (12 bytes **smaller**), entry offset still 16.
- New SHA256: `05e8ec1a91b92fff1cff0bea1a5926674b65f9a8a78f9868413c8df118d9492f`.
- Sotto K6 arena **14,976 bytes**, module callback stack reserve **1,040 bytes**, module envelope overhead 24 bytes, scratch requirement **4,080 bytes**. Available scratch is now **4,140 bytes**, giving **60 bytes margin** compared with the original 48.
- The resource codebook and IVF deck remain unchanged; the original catalog slot24 module is not overwritten.

The exact compiled native C divider was extracted from its production header and tested against Python integer division and remainder for all selected extreme boundaries and **one million deterministic random input pairs**. Additional cases cover zero, UINT32_MAX, powers of two, 87,260 and signed input handling through the unchanged shared divide core.

**The default module rebuild remains byte-for-byte identical** to the currently installed 9,784-byte stage-2 code, including its pinned SHA256.

## Actual ARM module / codec / RF simulation

The offline virtual loop uses the **real compiled Cortex-M0 module**, the exact pinned Sotto codec codebook+IVF deck, an independent native Sotto reference encoder, an RFIC FSK/QAM model, simulated GP0 8kHz PCM capture and DMA interrupts, and an independent compiled ARM packet decoder. SPI resource-read cost was fitted from physical device measurements (20-byte read 25.0us and 128-byte 126.56us). Frame and codec bitstream parity are strict.

The CPI and ISR-cost inputs are **model assumptions**, not observed CPU averages. Relative performance comes from changing only the module's division algorithm.

### 320 ms audio trials

| Simulated CPI / ISR cycles per PCM sample | Original GP0 module | Fast divider |
|---|---|---|
| 1.5 / 4,000 | Complete, byte-exact | Complete, byte-exact |
| 2.5 / 4,000 | PCM overflow after 1,600 samples | **2,560 samples completed** |
| 3.0 / 3,000 | PCM overflow after 1,280 samples | **2,560 samples completed** |

### 1.04-second audio trials

| Simulated CPI / ISR cycles per PCM sample | Original | Fast divider |
|---|---|---|
| 2.0 / 4,000 | 8,293 samples complete | 8,293 complete |
| 2.5 / 4,000 | Overflows at 1,920 samples | **8,293 complete** |
| 3.0 / 3,000 | Overflows at 1,600 samples | **8,293 complete** |
| 3.0 / 4,000 | Overflows at 1,200 samples | Overflows at 1,920 samples |

For all completed fast-divider cases, the decoded Sotto audio bitstream is **identical to native reference**, the independent ARM receiver reconstructs the complete BEGIN/VOICE/END packet session, with **six valid RF packets**, and the ring high-water is at most **2 of 4 blocks**. The harsher case shows this is not guaranteed to fix *every* possible CPU-contention profile; it retains proper `-12` abort and TX cleanup. An additional prefixed-frame RF test validates decoder offset behavior.

Evidence JSON:
- `docs/evidence/sotto-gp0-fast-div-short-20261126.json`
- `docs/evidence/sotto-gp0-fast-div-long-20261126.json`
- `docs/evidence/sotto-gp0-fast-div-cm0-profile-20261126.json`

Unit and RF regression tests: `tests/test_k6_sotto_fast_div.py` and `tests/test_k6_sotto_fast_div_rf.py`, plus the existing Sotto GP0 module / end-to-end suites. The full selected suite finished **103 passed**, with the intentionally pinned `K6_SOTTO_GP0_RELEASE_IMAGE` set to the original known-good KUL2 firmware. An initial run using the test fixture's stale pre-KUL2 `/tmp` firmware failed the packaging test as designed; rerunning with the correct KUL2 image passed all 103 tests. All tests were offline with no radio access.

## Candidate artifact and next gate

The unsigned **offline** candidate code and manifest are staged at:
`/storage5/dev/flux-sotto-gp0-kul2-verified-release-20261126/recovery/sotto-fast-div-offline-20261126/`

They are deliberately **not signed or installed**. Do not publish a signing key or casually overwrite the signed stage-2 library slot24: earlier oversized experimental Sotto modules froze the K6 and required battery removal. The baseline source/firmware, signed slot24 module, catalog slots25/30, and complete verified 2MiB SPI backup remain untouched.

Before any physical use, recheck the radio's existing module slot24 code hash and SPI backup, confirm the current diagnostic firmware still responds to cable protocol5, verify exactly one signed module envelope for the candidate, and ensure the K6 is on a dummy load with the operator present. Only then consider a controlled module swap, cold-boot/VFO check, readback, short manual PTT test and both `GPF1`/`GPT1` readouts. Preserve a signed rollback option. An offline passing model is **not proof** that live microphone Sotto TX works.

The longer-term goal remains sustainable continuous 8kHz PCM with valid on-air Sotto audio, not merely avoiding `GP0 ENGINE`.
