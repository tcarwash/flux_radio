# Flux live DV: PA11 actual ARMv6-M GPIO emulation and receive-mode interrupt constraints

2026-11-23, branch `research/offline-sotto-fsk-dv-20261123`. **OFFLINE means actual target ARMv6-M instructions executed under Unicorn**, not Python-only model logic. This experiment cross-compiled the unmodified `pa11adc.h` GPIO converter with clang `--target=thumbv6m-none-eabi -mcpu=cortex-m0 -Oz` and executed `pa11_probe()` with GPIOA register memory mapped at `0x48000000`. The same upstream Cortex-M0 instruction-cycle model used for Sotto measured both possible constant PA11 IDR values. There was **NO hardware access, serial lease, RF, MCU flash or module install**. Source wrapper: `tools/k6_pa11_armv6m_probe.c`; runner: `tools/k6_pa11_armv6m_emulate.py`; regression: `tests/test_k6_pa11_armv6m_emulate.py`.

## Actual PA11 code: instruction-cycle emulator

| GPIO turns per PCM sample | Actual compiled ARMv6-M instruction-model cycles | ADC equivalent CPU at 8kHz | Idle time in each 125µs sample interval |
| ---: | ---: | ---: | ---: |
| 80 | 5,445 | 43.56MHz | 68.28µs |
| **90** | **6,115** | **48.92MHz** | **61.30µs** |
| 98 | 6,651 | 53.21MHz | 55.72µs |
| 100 | 6,785 | 54.28MHz | 54.32µs |
| 110 | 7,455 | 59.64MHz | 47.34µs |
| 120 | 8,125 | 65.00MHz | 40.36µs |
| 200 | 13,485 | 107.88MHz | −15.47µs |

The emulator executes the **real GPIOA register writes and reads** from `pa11adc.h`, with `hold=3` and the two-input-level tests (IDR low/high). For each W, cycle counts were identical for both input levels, validating the loop's input-independent timing in the compiled source. The GPIO values are fixed bits in emulated memory: **this is not a sigma-delta analog electrical model** and makes no claim about PA11 audio quality, microphone gain or timing on actual hardware.

**Calibration mismatch matters.** The original **real** K6 microphone recorder captured 8164 samples at 7956.34Hz with W200, an observed equivalent **12,066 cycles/sample**, while this standalone ARMv6-M `-Oz` probe estimates **13,485 cycles** for W200 (~11.8% more). The original recorder was a different compilation/context and the emulator's simplified timing ignores real flash/bus/prefetch effects. Therefore do not claim 107.88MHz is physically measured. Treat the two sources as a **range**, not independent proof that the old physical capture was impossible. The original recorder did physically run at ~7956Hz, but without concurrently running a Sotto encoder.

## Two real source-code instruction traces, one shared CPU budget

The companion `tools/k6_sotto_slim_cm0_trace.py` runs the genuine ARMv6-M slim codec over 104 actual K6 microphone PCM blocks (104 × 10ms), producing 26/26 compressed Sotto superframes byte-for-byte identical to the host. A separate host-instrumented real-codec read counter records 3542 codebook vector accesses, max 191 in a single call. In `tools/k6_pa11_sotto_queue_sim.py --pa11-trace ... --cm0-trace ...`, **both ADC and codec cycle workloads now come from executing their compiled target ARM instructions**; the queue logic itself is a deterministic scheduling model, and each external SPI vector read still costs an assumed 34µs.

With **300 ISR cycles/sample** and a **5% CPU reserve**, the 104-block workload gives:

| W | 2 × 160B PCM buffers | 3 × 160B PCM buffers | 4 × 160B PCM buffers | Final codec calls completed |
| --- | --- | --- | --- | ---: |
| **90** | Overrun | **Overrun at block 50** | **No modeled overrun** | 104/104 |
| **80** | Overrun | **Overrun at block 98** | **No modeled overrun** | 104/104 |
| 100 | Overrun | Overrun | Overrun | 96/104 even with six buffers |
| 120 | Overrun | Overrun | Overrun | 67/104 even with six buffers |
| 200 | Overrun | Overrun | Overrun | 0/104 even with six buffers |

The previous **capture-calibrated** model predicted 3 buffers would suffice at W90; the stricter **standalone ARM GPIO** model predicts **4**. This discrepancy is a hardware-qualification gate, not a resolved performance number. **Prototype 4 PCM buffers and measure actual sample and encoder time on K6**, with enough room for larger held-out speech and 10ms worst-case variation. Four PCM buffers (640B) + two 64B RF packet buffers leave **~298B** of the previously audited module arena after the encoder's code/tables/state and 704B stack; this is insufficient for a robust ISR, metadata and guard state. Move **at least ~1KB** of static tables out of the arena and validate additional external SPI cost before building the full combined module.

**Important optimistic assumptions:** shape codebook SPI transfers are modeled as 34µs each and interruptible/preemptible, the actual centroid/cell-index fetches are **not included** if those tables move to SPI flash, and the radio's bus arbitration, timer interrupt exception latency, Flash wait-state changes and audio analog settling are not emulated. The 4-buffer result applies to **this specific 1.04s clip** only.

## Newly identified interrupt architecture constraint

`c/k6/mod/vecprobe.c`, `remapsoak.c` and `firmware/k6-aura/aura-pager.patch` show that the **KD32F328 Cortex-M0 has no VTOR** and normally vectors through the bootloader at `0x08000000`. An arbitrary module cannot just install a TIM17 or SysTick ISR. The known workaround maps an SRAM vector copy (`0x20000000`..`0x200000BF`) to address zero using `SYSCFG_CFGR1.MEM_MODE=3`. However, the production `hal::txdog` **only arms this remap while transmitting**; **while receiving** it restores main flash at zero (`MEM_MODE=2`) and masks TIM14's wrap IRQ, polling the watchdog's uptime instead. This is deliberate: on the physical K6, keeping the SRAM remap enabled **cleared FLASH_ACR.PRFTBE**, slowing flash code and FD6818 SPI reads substantially. The module API borrows `SYST` and the app while executing, and `api->delay_us` uses SysTick; hijacking SysTick or altering the SRAM table while the watchdog is armed risks disrupting timing and safety.

Therefore the first live-DV PA11 capture test should **not** assume an IRQ is available or silently replace the TX watchdog's table. Recommended staged architecture:

1. **Receive-only paced W90/W80 audio qualification first**, without codec or ISR. Use a free-running TIM17 96MHz counter (when proven free), wrap-safe 16-bit compare to start each sample at 12,000-cycle intervals, collect timestamp/jitter/overrun and raw/ADPCM PCM for offline comparison. The busy-wait between samples is acceptable for this *audio-only* calibration; no real-time codec claim. Guard if TIM17 is already in use. Preserve PA11 GPIO, amplifier PB2, REG30/47/48, TIM17 clock/registers, signed slot and both VFOs, TX state. **Microphone capture previously required a controlled keyed-but-PA-off setup** on this radio; do not assume the analog microphone path is available in ordinary RX without separately proving it. If a keyed-but-PA-off setup is required, follow the already proven MICVOICE fixture, bounded TX watchdog, dummy load, exclusive lock, cable-removal instructions, and queue protocol; do not describe that as receive-only.
2. **IRQ ownership audit/firmware design**: reserve a sample timer interrupt through the **firmware-owned** SRAM vector table, not an ad hoc module remap. Preserve HardFault, TIM14 TX watchdog, USART1 and SysTick handlers. For the keyed-but-PA-off mic phase, TX watchdog already maps SRAM at zero; verify it stays armed for the whole capture and never extends key-up. For RX-mode sampling, decide whether a separate guarded `ARM_AUDIO` remap reason is acceptable despite flash-prefetch cost, or if hardware DMA pacing/another path is necessary. Profile actual SPI codebook reads with the remap in the intended state before claiming 8kHz feasibility.
3. **Actual interrupt + codec emulation and physical trial**: a module with 4×80-sample PCM buffers, per-block sample deadline counters, maximum ISR duration, overflow/high-water and codec completion, no RF voice transmission initially. Verify actual Cortex-M0 interrupt entry/exit behavior and flash/SPI timing; a Python queue simulator is not enough. Include all added external IVF table reads, module RAM headroom, and radio restoration.
4. Only after these gates: integrate the already physically proven guarded 33-word native-FSK Sotto packet transport. Continuous FIFO refill remains a separate P0 problem; do not conflate finite-packet RF proof with live DV.

## Reproduction

```bash
S=/storage5/dev/flux-mic-digital-voice/sotto/c/fixed
OUT=/home/tyler/flux-bench/micvoice-20261123/pa11-m0-emulation-20261123
RES=$(clang -print-resource-dir)
clang --target=thumbv6m-none-eabi -mcpu=cortex-m0 -mthumb -mfloat-abi=soft \
  -nostdinc -isystem "$RES/include" -I /storage5/dev/flux-mic-digital-voice/c/k6/mod \
  -Oz -ffunction-sections -fdata-sections \
  -c tools/k6_pa11_armv6m_probe.c -o "$OUT/pa11.o"
ld.lld -T "$S/cm4/link.ld" --gc-sections -e pa11_probe -u pa11_probe \
  "$OUT/pa11.o" -o "$OUT/pa11.elf"
V=/home/tyler/flux-bench/micvoice-20261123/sotto-slim-cm0-build-20261123/.venv-cm0/bin/python
"$V" tools/k6_pa11_armv6m_emulate.py --elf "$OUT/pa11.elf" \
  --cm0-run "$S/cm0/run.py" --out /tmp/new-pa11-armv6m-report.json

B=/home/tyler/flux-bench/micvoice-20261123
python3 tools/k6_pa11_sotto_queue_sim.py --capture-meta "$B/micvoice-capture.json" \
  --cm0-trace "$B/sotto-slim-cm0-10ms-trace-20261123/arm-cm0-10ms-private-trace.csv" \
  --pa11-trace "$OUT/pa11-armv6m-cycles.json" --out /tmp/new-dual-armv6m-queue.json
python3 -m pytest -q tests/test_k6_pa11_armv6m_emulate.py
```

This experiment does not operate hardware or claim a lease. Private K6 speech samples and codec cycle traces stay in the bench artifact directory, not Git.
