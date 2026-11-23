# Flux live digital voice — measured slim Sotto Cortex-M0 *binary size* and RAM-budget model

Date: 2026-11-23, **OFFLINE**. Worktree `research/offline-sotto-fsk-dv-20261123`. The other agent owns the FSK-12 microphone-derived RF test bench lease, so no radio, SDR, serial, SPI or main firmware changes were made for this audit. The local `sotto` upstream sources in `/storage5/dev/flux-mic-digital-voice/sotto/` were read-only; a cross-compiled build was created **outside all Git worktrees** under `/home/tyler/flux-bench/micvoice-20261123/sotto-slim-cm0-build-20261123/` by setting `CM0_OUT`.

## A real ARM build — but not a real embedded timing measurement

We cross-compiled the existing upstream slim encoder with `--target=thumbv6m-none-eabi -mcpu=cortex-m0 -Oz`, using its native `tools/cm0_slim_build.sh` and the installed clang/lld toolchain. Output was an actual statically linked ARM EABI5 ELF, SHA256 `673687e1bd3a930aab74cba1a8f863f773c437d67d40e5a8b405010a61dd0f20`. ELF sections reported via `llvm-size -A`:

| ARM Cortex-M0 linked section | Bytes |
| --- | ---: |
| `.text` | **6,194** |
| `.rodata` | **35,016** |
| Total `.text` + `.rodata` | **41,210** |
| External shape codebook inside this *benchmark* ELF | **30,720** |
| Estimate of code and remaining read-only tables to reside inside K6 RAM module | **10,490** |

The benchmark ELF links the 30,720B codebook into `.rodata` for emulator convenience. The intended K6 runtime instead uses `SLIM_CB_FETCH` and libraries on **external SPI flash**. Subtracting that codebook gives an **upper-level static residency estimate**, not a measured on-radio K6 linked module. The source code's actual platform hooks, cell lists, ISR, FIFO driver, clock configuration and ABI are not implemented in this generic ELF.

Existing upstream [sotto-slim.md](https://github.com/baitisj/sotto/blob/main/docs/sotto-slim.md) provides the following **modeled** (not newly benchmarked here) numbers: **3,148B encoder state, 676B stack**, about **27/29MHz mean/worst compute demand** (Cortex-M0, code in RAM, `-Oz`) and around **150 external codebook vectors / 40ms**, roughly 5ms of SPI reads. A K6 has a **15,408-byte module execution arena**. Python Unicorn emulation is not installed in the active remote environment; no Cortex-M0 cycle/emulator or physical-stack proof was performed this turn.

## Conservative first RAM model

The simple model `10490 code/tables +3148 encoder state +676 stack` consumes **14,314B**, leaving **1,094B** before audio buffering, RF queue, hardware context, shared kernel/runtime state, alignment or interrupt stack. One 40ms 8kHz PCM block is `320 × 2 = 640B`. A 64-byte Flux L64 packet is 64B.

| Additional working buffers | Model's remaining bytes |
| --- | ---: |
| None | **1,094** |
| 1×40ms PCM + 1×64B packet | **390** |
| 1×40ms PCM + 2×64B packets | **326** |
| 2×40ms PCM, no packet | **−186 (does not fit)** |
| 2×40ms PCM + 2×64B packets | **−314 (does not fit)** |

**These are optimistic lower-bound allocations**: they do not account for the real microphone sampler's stack/interrupts, FIFO write buffers, radio UI/state, data-cache alignment, packet decoder state or system overhead. A positive 326B or 390B here is **not usable production headroom**.

## Engineering direction

**Do not** simply link this ELF into the K6 firmware and claim real-time DV. The first major milestone should be a K6-specific module interface that keeps the microphone sampler and encoder from starving each other:

- Move **at least ~1KB of extra read-only tables** from module RAM into the already-needed signed SPI library (e.g., upstream slim cell lists ~1.3KB, or regenerate the DCT cosine table using a 21-value basis) to provide margin for a second audio buffer and two L64 transmit buffers. Conservatively aim for **≥512B free after buffers**, not a 0B fit.
- Use a **timer/interrupt-backed 8kHz microphone sampler** or equivalent without long periods of sampling-disabled SPI. An 8kHz audio stream delivers a new signed PCM sample every **125µs**. Source's old ADC capture routine was a bounded standalone, mostly polling experiment; it is not a concurrent streaming acquisition interface. Ring buffers should be measured at 10ms / 80-sample resolution before committing to 2×40ms full blocks.
- Slim's API consumes **80 samples/10ms** via `slim_encode10`, with a 6-byte superframe completed every 40ms and additional pipeline latency. The modeled ~27MHz compute plus 5ms SPI per 40ms is a promising *average* CPU budget on the documented 96MHz K6 MCU, but must be timed in the actual firmware while keeping the sampler on schedule.
- Keep the **native FSK FIFO issue #9 separate**. Finite four-packet native 4800bps Sotto transport has previously passed RF for an external speech sample. The other agent is performing the first physical mic-derived packet transport; that is not yet resident MCU codec execution. True >64-word gapless FIFO streaming and `0x00F8` anomaly remain independent research gates.

## Verification and reproducibility

Read-only static model: `tools/k6_sotto_slim_mcu_budget.py` and `tests/test_k6_sotto_slim_mcu_budget.py`. Private numeric output `sotto-slim-cm0-budget-20261123.json` in the user's bench folder. Cross-compile command:

```bash
CM0_OUT=/home/tyler/flux-bench/micvoice-20261123/sotto-slim-cm0-build-20261123 \
 sh /storage5/dev/flux-mic-digital-voice/sotto/tools/cm0_slim_build.sh -Oz

llvm-size -A /home/tyler/flux-bench/micvoice-20261123/sotto-slim-cm0-build-20261123/slim.elf
python3 tools/k6_sotto_slim_mcu_budget.py \
 --elf /home/tyler/flux-bench/micvoice-20261123/sotto-slim-cm0-build-20261123/slim.elf \
 --out /tmp/slim-cm0-memory-recheck.json
```

**Verified:** real Cortex-M0 code generation and section footprint for a generic slim bench ELF, plus self-consistent memory projections. **Not verified:** actual resident K6 code size, simultaneous capture/encode, sub-125µs audio sampler interrupts, <40ms encode task completion, SPI flash contention, stack high-watermark or any live radio waveform.
