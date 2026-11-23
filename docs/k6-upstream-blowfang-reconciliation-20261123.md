# Blowfang upstream reconciliation — Flux modulation/DV audit (2026-11-23)

**Scope:** Inspect latest `baitisj/blowfang` upstream for missed FD6818B/K6 transmitter/receiver/modulation or digital-voice techniques. This is a **source and archived-evidence audit only**, with no live radio, SDR, serial port, SPI, firmware flash, or user worktree edits. The original 0x4819 K6 and the user's 0x4829 B-chip are **not electrically/firmware interchangeable without physical qualification**.

## Exact Git evidence and safe refresh

- Remote `upstream`: https://github.com/baitisj/blowfang.
- Previously cached `upstream/main`: [`6bb97ac715225f01ca68895cae0091bb7ce9b3b6`](https://github.com/baitisj/blowfang/commit/6bb97ac715225f01ca68895cae0091bb7ce9b3b6), 2026-11-20.
- **Refreshed `upstream/main`: [`d580eb067352191a69c75fa355890de89c9c136f`](https://github.com/baitisj/blowfang/commit/d580eb067352191a69c75fa355890de89c9c136f), 2026-11-23 15:11 PST**. Verified with GitHub's API and `git show upstream/main` after successful `git fetch --no-tags --filter=blob:none upstream main`. The first unconditional full-object fetch ran too long and was replaced with a filtered fetch. A separate clean shallow repository was also created as a read-only copy of this exact latest source.
- `6bb97ac..d580eb0` advances **102 upstream commits**, changing **133 paths**. **80** are in `radios/uv21/`; **five** `c/k6/` source paths changed (`k6_mod.h`, `micgain.c`, `mickrec.c`, newly added `micsotto.c` and `micspi.c`), plus two `firmware/k6-aura/` files. Remaining edits are mostly Pluto SDR tooling, bench diagnostics, documentation, tests and CI.
- **No merge or cherry-pick into the user's Flux `main`, active B-chip source worktrees, or firmware.** The original Git directory's upstream tracking ref was refreshed, but its checked-out HEAD and pre-existing user changes were preserved. The audit lives in an isolated worktree/branch, not a competing TX/DV feature branch.

## Most important source-backed finding: upstream already encoded live K6 microphone audio

**Priority P1 — borrow an actual physical algorithm, not only a theoretical CPU budget.**

The latest source [`c/k6/mod/micsotto.c`](https://github.com/baitisj/blowfang/blob/d580eb067352191a69c75fa355890de89c9c136f/c/k6/mod/micsotto.c), [hardware chronicle `docs/k6-mic-probe.md`](https://github.com/baitisj/blowfang/blob/d580eb067352191a69c75fa355890de89c9c136f/docs/k6-mic-probe.md) and its [`micspi.c` wrapper](https://github.com/baitisj/blowfang/blob/d580eb067352191a69c75fa355890de89c9c136f/c/k6/mod/micspi.c) demonstrate or stage:

- The original (non-B) K6 **keyed, PA OFF, programming cable OUT**, mic ADC / AF DAC enabled in `REG30`, and `REG47` AF select 4. `REG7D` mic sensitivity 31 is a strong analog setting; the digital PA11 one-pin ADC recovers actual local microphone audio. Earlier upstream prose that says “no mic path” is **explicitly superseded by the later micaf/micsotto result** in the same document.
- **TIM17 IRQ at 8 kHz**, with **48 PA11 sigma-delta turns** per sample (`W_IRQ=48`). An ISR puts PCM into a **240-sample (three-block) ring**, while the foreground encodes successive **80-sample/10-ms blocks** with `slim_encode10`. The codebook and indexed cell deck reside in external SPI flash, and a bounded interruptible direct SPI1 read routine prevents interrupt starvation. The ISR also implements simple DC tracking and de-emphasis.
- **Physical original K6 `msot1b` result:** **86 Sotto-1200 superframes, 3.4 seconds recorded, zero dropped 10-ms blocks**; sampling interrupt occupied approximately **28% of CPU**, SPI codebook reads **11%**. At `W_IRQ=96`, an earlier physical `msot1a` ran at **54% IRQ CPU** and lost **97 of ~480 blocks (~20%)**. Human listener heard the coded voice played back on the original radio's speaker. These are actual original 0x4819-radio observations, **not B-chip proofs**, nor a sustained over-the-air real-time DV link.
- **Transferable architecture:** TIM17 update IRQ + bounded PA11 sampling + three-block ring + SPI codebook reads + foreground Sotto encoding. Prior Flux B-chip offline [live-DV scheduler/PA11 ARM audit](https://github.com/tcarwash/flux_radio/issues/10) models W80/W90 and a codec/SPI burst workload. **Use upstream W48 as a *new implementation and audio-quality baseline***, then emulate the actual B-target compiled ISR and codec together, test PA11 analogue noise/SINAD and encoder packetization separately, and benchmark observed B interrupt/flash/CPU utilization before promising continuous coding.
- **Critical limitation:** capture requires **keyed receive-state workaround with PA OFF**. Thus microphone capture + Sotto encoding can coexist on original K6, but **microphone capture and RF PA-on transmission do not coexist in this proof**. The actual live-DV architecture needs capture, queue/store, dekey, and separate guarded native-FSK RF delivery; this is evidence for **store-and-forward or half-duplex staged operation**, not live concurrent on-air microphone encoding/transmit.

**Follow-up owner:** existing [live-DV #10](https://github.com/tcarwash/flux_radio/issues/10), [hardware mic #12](https://github.com/tcarwash/flux_radio/issues/12), and existing scheduler branch; don't spawn a duplicate owner or modify their worktrees without coordination.

## Significant companion: level-15 SPI outbox while keyed, but PA off

The new upstream [Aura pager patch](https://github.com/baitisj/blowfang/blob/d580eb067352191a69c75fa355890de89c9c136f/firmware/k6-aura/aura-pager.patch) changes the firmware `mod_fl_prog` guard: on **API level 15**, a page-sized program may be permitted in the reserved outbox **0x190000–0x1A0000** *while keyed only with REG36 PA off*. `fl_erase` still cannot run while keyed. The source comment gives roughly millisecond page-programming, and [`micspi.c`](https://github.com/baitisj/blowfang/blob/d580eb067352191a69c75fa355890de89c9c136f/c/k6/mod/micspi.c) configures `MS_SPI=1`, up to **60 s** of 6-byte/40-ms Sotto output with outbox header committed last; cue/record/return is cable-out.

**Evidence boundary:** latest docs describe the `micspi` extended design, **but do not provide a matching 60-second lossless physical B-chip run**. This is a candidate for durable compressed packet storage, *not proof of 60s or live on-air RF*. It requires an intentional, safety-reviewed **B-chip firmware API upgrade** plus exact SPI memory map / erase bounds / PA guard / crash-resume / wear / provenance, rather than copying level15 writes into the current API3 TX bench. Keep firmware flashing and bench transactions under the shared queue.

## Independent RF corroboration of current TX-24B investigation (older upstream measurement)

The original FD6818 upstream [`c4probe.c` P2 and `docs/k6-c4fm.md` run 1](https://github.com/baitisj/blowfang/blob/d580eb067352191a69c75fa355890de89c9c136f/docs/k6-c4fm.md) physically changed **REG40[7:0] while native 4800-baud FSK was active**. Its real SDR discriminator showed the deviation at about **0.70× baseline when reduced to one sixth**. This is upstream **0x4819** proof for the very *mode coexistence* that we have intentionally not claimed on **0x4829** in our [TX-24B #26](https://github.com/tcarwash/flux_radio/issues/26). Meanwhile our [TX-24 B-chip one-shot tone-FM experiment](https://github.com/tcarwash/flux_radio/issues/17) *has* physically confirmed four REG40 deviation levels on the B chip.

**Conclusion:** TX-24B is no longer a vague guess: both component effects and the original native-FSK coexistence have physical evidence, **but not on the same B-chip mode**. The already frozen B-chip TX-24B finite-frame source and holdout discriminator should be preserved without changing the pass thresholds. Its owner still needs a separately authorized lease after priority experiments. Do not confuse this with the separately owned [TX-26 per-symbol REG70 4-FSK](https://github.com/tcarwash/flux_radio/issues/19).

## Already found in Flux queue — do not duplicate

| Upstream source-backed technique | Current state in Flux / remaining gap |
|---|---|
| Native FSK FIFO sign + symbol-rate `REG70[6:0]` magnitude → 4800-baud C4FM/YSF/DMR | [TX-26 #19](https://github.com/tcarwash/flux_radio/issues/19), B hardware still needs independent proof; original source and timing are real for 0x4819 |
| `REG5E[9:3]` FIFO almost-empty threshold, `REG02` zero-before-read IRQ latch, `REG3F` bit14 AE IRQ | [P0 FIFO #9](https://github.com/tcarwash/flux_radio/issues/9), active source/emulator; retain B-specific physical qualification |
| Chip's free-running FSK bit grid and MCU RC frequency drift; TX-done/IRQ timing corrections | [#9](https://github.com/tcarwash/flux_radio/issues/9), [#15](https://github.com/tcarwash/flux_radio/issues/15) and [TX-26 #19](https://github.com/tcarwash/flux_radio/issues/19). Old 0x4819 drift values are not B timing calibration |
| Live FM-RX→Sotto encoding via DMA/IRQ, direct interruptible SPI1 reads, codebook deck | [Sotto/live-DV #10](https://github.com/tcarwash/flux_radio/issues/10), existing B target emulator efforts; the newly exposed original **MIC→Sotto** physical proof above should now be brought into its architecture |
| `REG51` signed subaudio frequency DAC; `REG52` CTCSS phase; `REG36/19` RF PA internal gain; GP0/PB1; fast-SPI RFIC writes; FFSK 2400 and 6348-baud native modes; PA11 full-band discriminator | [TX-27 #20](https://github.com/tcarwash/flux_radio/issues/20) through [TX-31 #25](https://github.com/tcarwash/flux_radio/issues/25), including RX-01 #24, plus earlier Flux TX16 studies. These were cataloged before this refreshed upstream comparison |

The separately added [UV-21 FD6818B driver](https://github.com/baitisj/blowfang/blob/d580eb067352191a69c75fa355890de89c9c136f/radios/uv21/fw/src/drivers/fd6818b.rs) is useful **corroborating 0x4829 source** but is not a new K6 waveform port: its **65** named register constants overlap the existing K6 B-driver's **64** names almost exactly; additions are `REG_AGC_MODE=0x7E` and a rename of `REG_RXAGC_7=0x7B` to `REG_RSSI_TABLE_LO=0x7B`. Most changes concern an entirely different board (PA path GPIO switches, APC/power tables, 5RH/UV21 pinout, AT32F421 rather than K6 microcontroller). **Do not copy its PA, GPIO, band or oscillator settings into K6 without board-specific physical evidence.**

## One useful undertracked optimization: shortened FSK framing

Original upstream [`c4lock.c`](https://github.com/baitisj/blowfang/blob/d580eb067352191a69c75fa355890de89c9c136f/c/k6/mod/c4lock.c) defines `CH_CTRL=0x0000` for a **1-byte preamble / 2-byte sync** instead of the familiar `CTRL_BASE=0x00F8` for **16-byte preamble / 4-byte sync**. At 4800 symbols/s this is **24 bits = 5.00 ms** versus **160 bits = 33.33 ms**, a calculated **28.33-ms RF framing-overhead reduction per finite frame**. It does *not* establish an equivalent reduction of the entire rearming gap or any B-chip success.

All Flux **physically validated B-chip Sotto 33-word L64 guarded frames currently use the long public marker** and a sometimes extra `f8 00` word whose location depends on FIFO alignment. Short framing may alter the emitted `REG59`-prefix artifact and chip acquisition, so simply setting `0x0000` in a live DV source without modifying the independent B receiver and guard scorer is **unsafe scientifically**.

Created independent [unranked FSK-15 issue #27](https://github.com/tcarwash/flux_radio/issues/27) for an **offline-first, separate B-chip 0x00F8/0x0000 matched known-frame trial**, after P0 FIFO #9 and prefix root-cause #15. No hardware lease or source conflicts.

## Other recent upstream changes: relevant context, not new B-chip TX vectors

- `tools/pluto_bench.py` cue improvements (`--min-s`, `--max-s`, `--min-peak`) support **cable-out mic experiments** in which serial logging is unavailable; useful inspiration for the existing Flux experiment queue, but not a substitute for the serial lock or operator lease.
- Added Pluto SDR channel/PSD tools, firmware safe-crystal handling, and new CI jobs: potentially useful observability and reproducibility, **not direct K6 RF waveform actuators**.
- The majority of the 102 commits add a **UV-21/5RH-portable firmware platform** and its 80 files. That board's reverse engineering, SPI driver and receive-only UI are unrelated to this K6's physical TX silicon capabilities. No reason to bulk-merge those board dependencies into Flux's active `main`.

## Concrete next actions, ordered without disrupting active work

1. **Live-DV #10:** audit and port the upstream *W48 TIM17 microphone IRQ + 240-PCM-sample / 80-sample encode scheduling* into a separate B-compatible emulator branch; compare real compiled ISR cycle and flash/Sotto costs, then B microphone audio quality under a new approved lease. Preserve the independent on-air TX/restore rule; no assumption that PA11 microphone audio is available while PA is transmitting.
2. **Durable outbox:** consider level15 `micspi` as a *store-and-forward buffer* after reproducing a B-safe memory layout, authenticated module APIs and keyed-PA-off page guard; do not flash firmware or program the outbox under the current audit.
3. **TX-24B #26:** preserve its existing 8×33 finite-frame B source and precommitted physical scorer; original upstream P2 adds evidence but is not a substitute for the B hardware trial.
4. **P0 #9 / #15 then FSK-15 #27:** finish FIFO AE/leading-prefix root cause, then qualify shortened `REG59` framing as an independent throughput optimization.
5. **TX-26 #19 and modem vector queue:** independently stage B-native 4800-symbol/s 4FSK; existing TX-27..31 and RX-01 issues cover the other promising source-backed control points.

**No missing new high-confidence *K6 B-chip arbitrary RF/IQ hardware input* emerged from the 102 new upstream commits. The consequential unintegrated capability is a working original-K6 microphone/interrupt-driven Sotto-1200 encoder path, plus a useful but separate short-FSK-framing optimization.**
