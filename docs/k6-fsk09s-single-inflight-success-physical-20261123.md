# Flux FSK-09S-D — first physically demonstrated FD6818B in-flight FIFO append

Date: 2026-11-23. Result **PASS for one in-flight 16-bit FIFO write**, not yet repeated appends, gapless >64-word streaming, or automatic FIFO headroom detection. Precommitted standalone experiment [commit `c2ae142`](https://github.com/tcarwash/flux_radio/commit/c2ae142) and [strict criterion](k6-fsk09s-single-inflight-precommitted-20261123.md).

## What changed and why

The preceding two-frame FSK-09S-C experiment returned firmware result 98 but had an extra `0x00F8` leading word in the second frame, so its 49th marker was displaced outside the declared length and the first physical RF evidence was **negative**. See [first negative report](k6-fsk09s-oneword-first-negative-physical-20261123.md). The separate FSK-09S-D test retained the identical second frame's **48 originally preloaded words and 49th `0xA53C` marker**, programmed 49-word length, 4,800-bps native FSK, and a **single in-flight FIFO write at >=70ms after the TX-enable command**, but transmitted it as **the only hardware frame**. No previous frame completion or rearm could contaminate the FIFO at the start.

We never wrote more than **49 words total** since FIFO clear, a bound below the 64 words already physically demonstrated as an initial TX preload. This isolated experiment did **not** rely on interpreting `REG_02` AE flags, reading destructive `REG_5F`, guessing occupancy, writing speculative `REG_5E` thresholds, or manipulating PLL/IQ waveforms. TX was bounded by an existing 1900-ms watchdog and source-level completion polling timeout of 700ms. The exact C source/module digest were frozen before the conducted experiment.

## Hardware / RF result

| Measurement | Verified finding |
| --- | --- |
| RFIC and firmware | FD6818B chip `0x4829`, build `95d6b260d4122287` |
| Fixture | User-confirmed dummy-loaded 433.500MHz K6, air-coupled RTL-SDR 433.750MHz, 1.024 Msps |
| Configured hardware frame | **49 words, 98 bytes**, `REG_5D=0x6100` |
| FIFO before TX enable | **48 words** |
| Additional FIFO words after TX enable | **1**, 16-bit value `0xA53C` after waiting >=70ms |
| Module result | **49 complete** (necessary, not sufficient) |
| Public sync acquisition | **1** frame located using only hardware 16×0x55 preamble + `07 6B 3E 36` sync |
| Independent RF bit recovery | **784/784** payload bits exactly match frozen truth; 0 errors |
| Last word received in RF | **0xA53C** (bytes `3c a5`), exactly matching after-start FIFO write |
| Receiver synchronization correlation | **0.993249** |
| Original slot30 signed code backup/reinstall/readback | **Verified** |
| Both VFOs restored to 144.390MHz / final TX state | **Verified / RX idle** |

The receiver used *only publicly known native RFIC framing* to determine start and FSK decision levels. It did **not** need any known payload prefix, nor did it receive the expected last marker for timing/synchronization; exact comparison to 784 expected bits was performed **after** demodulation. The hardware-module return and both 784-bit recovered RF payload independently support the same conclusion: **one word appended while native FSK TX was already active was transmitted at the correct end-of-frame bit position**.

## Safety, evidence, repeatability

One separate guarded serial transaction acquired the **same canonical Flux nonblocking process-scoped lock** as concurrent RF agents, backed up the known signed slot30 PDM code, installed the frozen 1208B test module, ran one hard watchdog-bounded finite frame, then restored and verified trusted slot code and both VFOs. No MCU firmware flash, automatic RF retries, unbounded TX or modifications to another agent's worktree.

The prospective armed source SHA256 is `7c6a90b2bb91f6304df7851741955df8a417f22b089479c4dfd221c9c21a7fea`; compiled module SHA256 `63c7b7597136d4c3d5c4d3b14851445edd8c968cd7ae52a3578e76e3f726b6e1`, 1208 bytes.

Physical evidence preserved in `/home/tyler/flux-bench/tx-injection-20261122/fsk09s-oneword/`:

- `fsk09s-single-run01.npy` original air-coupled SDR IQ SHA256 `fa31e32931ade4f10d3d1af702bd32590cb94ff75da3a86d11ae1e85ffc55e00`.
- `fsk09s-single-run01.json` full signed module, TX safety, tuner, SDR preflight, module return and restoration telemetry.
- `fsk09s-single-analysis-run01.json` independent hardware-sync RF recovery, decoded complete frame, 0-bit-error score and correct marker.

Read-only reproducible re-analysis:

```bash
python3 tools/k6_fsk_oneword_single_analyze.py \
 --iq /home/tyler/flux-bench/tx-injection-20261122/fsk09s-oneword/fsk09s-single-run01.npy \
 --out /tmp/fsk09s-single-reanalysis.json
```

## What remains unproven

- **Multiple** append operations in one frame: only one 16-bit write was tested and verified.
- **More than 64 words** of total FIFO output per frame: total written was 49, so this does not establish FIFO drain/refill capability beyond preexisting capacity.
- A **reliable B-specific FIFO-almost-empty/occupancy signal**, which both old-family IRQ and newer status-only tests failed to expose.
- **Transparent, gapless, indefinite FSK** or arbitrarily generated RF IQ.
- A successful **mid-frame append after a previous modem frame rearm**: FSK-09S-C failed, potentially because of a lead-in FIFO word. That anomaly needs its own cleanup/sequencing audit.

## Next frozen physical experiment

A newly designed **one-frame 64-word test** with 48 words initially preloaded, followed by four 4-word append batches at safe fixed times <= initial 48-word duration, would keep total writes <=64 **even if no FIFO drain occurs**, avoiding the unknown headroom register issue. That can test **multiple** in-flight writes and ordering before any test above the demonstrated 64-word FIFO capacity. It must be independently frozen, safety-reviewed, then verified by public-sync-only RF recovery before any >64-word streaming claims.

**Bottom line:** The FD6818B TX FIFO is **not inherently write-once at the start of the native-FSK frame**. A one-word in-flight data append now has direct RF proof. This is a meaningful path toward software-scheduled FSK symbol streaming even without FIFO almost-empty IRQ, but the much harder >FIFO-capacity sustained stream remains an open experiment.
