# Flux live DV — actual PA11 capture timing vs Cortex-M0 slim Sotto instruction cycles and codebook SPI bursts

Date: 2026-11-23. **OFFLINE cycle-model / prior recorded K6 microphone analysis** on `research/offline-sotto-fsk-dv-20261123`. No radio, serial port, GPIO, SDR, PTT, module SPI flash or firmware changes this experiment. The separate FSK-13/14 guarded native-FSK PHY agent independently completed two **strict RF passes** during this offline work. This research did **not** claim its hardware lease or alter its source or RF fixture.

## New finding: the proven PA11 microphone recorder uses essentially the whole CPU

The actual 0x4829 K6 microphone's previously archived `micvoice-capture.json` reports **8,164 samples in 10,261×100µs = 1.0261 seconds**, a measured **7,956.339538 Hz**, `turns=200`, zero clipped samples. This source is read-only and the original microphone audio remains private. The previously proven converter is a **software-driven, one-pin PA11 sigma-delta sampler**, *not a hardware ADC/DMA peripheral*: `c/k6/mod/pa11adc.h` calls `pa11_turns` for **200 GPIO drive/sample cycles per PCM sample**, with `hold=3` and a source-comment instruction estimate **44+5×hold = 59 MCU cycles per turn**. `micvoice.c` records in a tight polling loop; it does **not** interrupt/resume the codec, and it serially performs IMA ADPCM after each sample.

The actual capture period implies **12,066 CPU cycles per PCM sample**, or **60.329 cycles/turn equivalent** (includes the converter and surrounding processing). At 8,000 samples/s, the unchanged 200-turn sampler would demand approximately **96.53 MHz equivalent CPU** *by itself* on the 96 MHz KD32F328 Cortex-M0. The source-comment 59-cycle estimate gives **94.4 MHz** (98.3% CPU) at 8 kHz, still leaving essentially no space for Sotto or SPI. The observed capture's rate is 0.546% below 8kHz, and the previous offline Sotto conversion explicitly corrected that rate.

**Therefore the previously working PA11 capture routine cannot simply be called alongside the slim encoder to achieve live digital voice.** A 10ms PCM ring buffer does not cure a ~100% busy-wait sampler; it needs a shorter, timer-paced conversion burst or hardware-assisted sampling. A 90-turn version of PA11 was previously characterized in a *different* 17.8kHz receive-audio test, but **90 turns paced at 8kHz with real microphone input has not been physically tested**. That analog sample/hold change could affect gain, noise, distortion and clock accuracy.

## Reproducible real-capture CPU budget (sensitivity model)

`tools/k6_pa11_sotto_realtime_budget.py` uses the recorded capture's 60.329 equivalent cycles/turn, target 8kHz, and the upstream slim codec's **29 MHz modeled worst 40ms-frame compute**. It separately charges **150 codebook reads/40ms × 34µs/read** (12.24 MHz equivalent), a hypothetical **300 cycles/sample ISR** (2.4 MHz), and a **5% CPU reserve** (4.8 MHz). These are explicitly **assumptions**, not measured interrupt and SPI timing on this K6.

| PA11 turns / 8kHz sample | ADC equivalent MHz | Total CPU demand MHz (conservative modeled) | Remaining MHz | Average-fit gate |
| ---: | ---: | ---: | ---: | --- |
| **200** (actual old sampler) | **96.53** | **144.97** | **−48.97** | **NO** |
| 120 | 57.92 | 106.36 | −10.36 | NO |
| 110 | 53.09 | 101.53 | −5.53 | NO |
| 100 | 48.26 | 96.70 | −0.70 | NO |
| 98 | 47.30 | 95.74 | +0.26 | Mathematical fit, not practical margin |
| **90** (candidate) | **43.44** | **91.88** | **+4.12** | **YES, only average** |
| 80 | 38.61 | 87.05 | +8.95 | YES, only average |

A 90-turn sample would keep PA11 actively switching for ~**56.56µs** of each **125µs** period, leaving about **68.44µs** for other work. A high-priority 8kHz interrupt (or hardware event) is required to keep sample periods regular; the current busy-wait `pa11_turns` loop is not a tested ISR. The source's GPIO drive and SPI codebook operations may interfere with interrupt latency; this model alone cannot establish whether it is safe to interrupt an active SPI transaction. An offline sensitivity sweep shows that **if** 34µs codebook transfers were uninterruptible, sample deadlines could be delayed by up to 34µs. No claim about the real SPI IRQ mask is made.

## New genuine codec instrumentation: codebook reads are strongly bursty

We extended `tools/k6_sotto_slim_streaming_host.c` with an **optional `-DSLIM_TUNE` instrumentation CSV** of the actual `slim_stat[2]` codebook fetch counter after every `slim_encode10` call. This does not modify the upstream codec. The instrumented encoder was run on the **same 1.04-second actual K6 microphone PCM**, and the compressed output was **bit-for-bit identical** to the previously committed slim reference hash `8c20829a11ce6c3f22162b6dcc15e0f4378bbdccfbdf967a6eb30b0bd4646ba5`.

| Codebook read observation | Result |
| --- | ---: |
| `slim_encode10` calls (80 samples / 10ms) | **104** |
| Total codebook vector reads | **3,542** |
| Mean reads / 10ms call | **34.06** |
| Maximum reads in a single 10ms call | **191** |
| Calls with nonzero reads | **25** |
| Read distribution | **ALL on the first call of each 40ms group, after startup** |
| Largest single-call SPI cost, at 34µs/read | **6.494ms** |

This is more important than the mean SPI estimate. Even at 90 PA11 turns, **6.494ms of estimated codebook reads + 4.525ms of ADC GPIO conversion already exceeds 10ms**, before the encoder's arithmetic or interrupt overhead. A strict single-10ms-buffer processing design is therefore not viable at the measured peak. The per-call read counts are *measured from the genuine host C codec path*; the SPI time remains the earlier K6 hardware estimate, not a new measurement. **Important optimistic omission:** if the IVF centroid and cell-index tables are also externalized from module RAM, their SPI accesses are additional to these `slim_stat[2]` shape-vector fetches. No complete K6 library read trace has been collected.

## New actual Cortex-M0 instruction-level emulation on the same K6 microphone clip

We installed Unicorn 2.1.4 and Capstone in a **private isolated virtual environment outside the repository** and ran the existing upstream Cortex-M0 timing model against our previously compiled ARMv6-M `-Oz` ELF, **not** the host CPU. We first verified a 4-superframe K6 sample: emulator and host bits matched exactly. Then our new `tools/k6_sotto_slim_cm0_trace.py` called the genuine ARM `slim_encode10` **104 times** on each successive 80-sample K6 PCM block, counted instruction cycles, flushed at the end, and verified **all 26 compressed superframes bit-exact to the host**. Code and codebook are modeled as in-RAM (`ws=0`); the codebook's external SPI transfer cost is charged separately from the host-instrumented read counts.

| ARMv6-M model measurement (real K6 audio) | Result |
| --- | ---: |
| Calls | **104 × 10ms** |
| Encoder equivalent CPU mean | **24.90 MHz** |
| Maximum 10ms call | **312,662 cycles (31.27MHz equivalent)** |
| Maximum 40ms combined codec equivalent | **25.99 MHz** |
| Encoder state | **3,148B** |
| Stack high-water mark in emulator | **704B** (earlier upstream 676B was from a different benchmark) |
| Maximum combined encoder cycles + modeled SPI, one 10ms call | **905,373 cycles (9.431ms at 96MHz)** |
| Compressed voice recovered | **26/26 Sotto frames, bit-exact** |

**These are instruction-emulator results, not measured Cortex-M0 hardware execution times.** The real flash driver, interrupts, SPI timing, PA11 GPIO conversion and module ABI were not run in Unicorn. No physical 8kHz concurrent recording/encoding was done. All private PCM, voice bitstream, per-call instruction trace and codebook count CSV remain local under `/home/tyler/flux-bench/micvoice-20261123/`, not committed.

The per-call codec computation itself is uneven: its four 10ms phase averages are approximately **25.07, 30.18, 13.47, 30.87 MHz**. Codebook reads cluster in phase 0. The low-compute phase 2 helps the model recover backlog, but it does not make each phase meet a strict 10ms processing deadline.

## PCM buffer sensitivity with the *actual* ARM model and actual read-count trace

The deterministic `tools/k6_pa11_sotto_queue_sim.py` models an interrupt-preemptible 8kHz sampler and a FIFO queue of 10ms/160-byte PCM blocks. For each block it subtracts the proposed PA11 sample cycles, 300-cycle/sample ISR assumption and 5% reserve from the 96MHz CPU, then spends the remaining cycles on the **actual 104-call ARM emulator cycle trace plus actual codebook-read counts charged 34µs each**. A buffer is considered overrun when the producer needs a new buffer but all others are pending codec work. This is a *scheduling model*, not physical IRQ preemption or SPI transaction execution.

| Candidate | 2×160B PCM buffers | 3×160B PCM buffers | 4×160B PCM buffers |
| --- | --- | --- | --- |
| 90 turns, real-clip ARM+SPI trace | **Overrun at 10ms block 5** | **No modeled overrun** | No modeled overrun |
| 80 turns, real-clip ARM+SPI trace | **Overrun at block 5** | **No modeled overrun** | No modeled overrun |

All 104 codec calls were completed in the three-buffer, 90-turn **idealized model**, with no remaining work at the end. It assumes that 34µs shape-codebook reads are schedulable/preemptible and excludes any additional SPI fetches for externally stored IVF centroids/cell lists. A separate conservative worst-average CPU sensitivity model previously required four buffers under some synthetic burst patterns, illustrating how different input speech or codebook search distributions can change the answer. **Three buffers is not a guaranteed production minimum** until a larger held-out speech corpus and real interrupt/stack profiling are run.

The actual Cortex-M0 ELF memory audit showed **10,490B** of resident code/tables after externalizing the 30,720B codebook. Replacing the earlier estimated 676B stack with this emulation's **704B** gives `10,490 code/tables + 3,148 state + 704 stack = 14,342B` of the K6's **15,408B** module arena. Three 160B PCM buffers + two 64B L64 packet buffers consume another **608B**, leaving **458B** before any GPIO ISR, scheduler, packet state, SPI scratch, alignment or other runtime. Four PCM buffers leave **298B**. Therefore at least **~1KB of additional read-only tables must be externalized or code reduced**, and a more conservative runtime margin is desirable.

## Engineering decision and next experiments

**The viable next architecture is not the old 200-turn polling ADC plus an encoder.** It is a **timer-paced, preemptible short-burst PA11 sampler** (initially W=90 for a receive-only audio test), with a minimum **three 160-byte/10ms PCM buffer pool** in the current real-clip model, an incremental slim encoder in the main loop, and codebook reads that can tolerate the sample deadline or be prefetched/scheduled. The current module must be slimmed to make room for interrupts and buffers. Even that is only a hypothesis until the radio's analog PA11 signal survives the change.

Before any live-DV transmit experiment, queue a **separate receive-only, PA-off** K6 microphone timing trial with an exact 8kHz deadline recorder and measured sample-jitter/overrun counters. Compare 200-turn baseline (recorded) to W=90 and perhaps W=80 in a new physical test under the exclusive hardware lease. Verify restored original GPIO/REG30/slot/VFO state, real audio SNR and speech-band quality, and measured simultaneous codebook fetch interrupt safety. Do not claim a working live encoder, an FSK bearer or actual 681ms end-to-end audio latency based on these offline models.

**Parallel physical RF update (independently reported by the other agent after this CPU trace was collected):** [FSK-13 guard33](https://github.com/tcarwash/flux_radio/blob/research/live-dv-fifo-rootcause-20261123/docs/k6-fsk13-guard33-sotto-physical-pass-20261123.md) **STRICT PASS**, 8/8 CRC-valid finite Sotto packets, 0/4096 packet bit errors, offsets 2/0 alternating; [FSK-14 real-K6-microphone guarded packet test](https://github.com/tcarwash/flux_radio/issues/18) **STRICT PASS**, 4/4 CRC-valid real-mic Sotto packets, 0/2048 packet bit errors, offsets 2/0/2/0. The `0x00F8` anomaly is still present but is **successfully contained by the 33rd sacrificial word** in both bounded physical experiments. Each physical run was conducted by the other agent with a separate exclusive lease and verified signed-slot/VFO restoration. **The finite native-FSK voice packet bearer is now physically proven**, not continuous >64-word FIFO streaming and not real-time on-radio codec execution. The source-level cause of the inserted word remains open in #15. Our CPU/sampling branch performed **NO RF TRANSACTION** and holds no hardware lease.

### Reproduce (private K6 mic files stay on the bench)

```bash
python3 tools/k6_pa11_sotto_realtime_budget.py \
  --capture-meta /home/tyler/flux-bench/micvoice-20261123/micvoice-capture.json \
  --out /tmp/pa11-sotto-budget.json

# Host codebook instrumentation: build genuine codec with -DSLIM_TUNE.
S=/storage5/dev/flux-mic-digital-voice/sotto/c/fixed
clang -O2 -DSOTTO_V36 -DSOTTO_PITCH_THIRD -DFX_NARROW_MUL -DSLIM_TUNE \
  -I "$S" tools/k6_sotto_slim_streaming_host.c "$S/sotto_fx_slim.c" \
  "$S/fx_tables.c" -lm -o /tmp/slim-instrumented

# The ARM ELF and Unicorn venv were created outside the Git worktree.
B=/home/tyler/flux-bench/micvoice-20261123/sotto-slim-cm0-build-20261123
"$B/.venv-cm0/bin/python" tools/k6_sotto_slim_cm0_trace.py \
  --elf "$B/slim.elf" --cm0-run "$S/cm0/run.py" \
  --pcm /home/tyler/flux-bench/micvoice-20261123/sotto-offline-run01/k6mic-resampled-8k.raw \
  --baseline /home/tyler/flux-bench/micvoice-20261123/sotto-slim-packet-run01/slim-sotto1200.bit \
  --reads "$B/mic-incremental-codebook-reads.csv" \
  --out-dir /tmp/flux-cm0-new-trace

python3 tools/k6_pa11_sotto_queue_sim.py \
  --capture-meta /home/tyler/flux-bench/micvoice-20261123/micvoice-capture.json \
  --cm0-trace /home/tyler/flux-bench/micvoice-20261123/sotto-slim-cm0-10ms-trace-20261123/arm-cm0-10ms-private-trace.csv \
  --out /tmp/flux-slim-queue-report.json
python3 -m pytest -q tests/test_k6_pa11_sotto_realtime_budget.py \
  tests/test_k6_pa11_sotto_queue_sim.py tests/test_k6_sotto_slim_cm0_trace.py \
  tests/test_k6_sotto_slim_host_incremental.py
```

All tests and scripts are offline. No private raw microphone audio, compressed voice bits or per-call trace were committed.
