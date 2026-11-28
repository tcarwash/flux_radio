# K6 microphone: preemptible DMA/8kHz PCM producer for Sotto

**2026-11-25.** Branch `feature/sotto-mic-continuous-pa-20261125`. Offline implementation ready for attended *no-RF* bench validation; not yet a real Sotto speech transmission.

## Motivation and measured basis

The external K6 microphone reaches its FD6818B RFIC, and the previously verified physical GP0 selector12 → PB1 bitstream contains frequency-correlated microphone content. `MIC PCM` converted it to **1,824 samples of signed 8kHz PCM** in each of three real test runs; their 1kHz/2kHz measurements followed the generator, while the generator-OFF run had a lower RMS and VOX floor. See `docs/k6-sotto-gp0-mic-pcm-20261125.md` for all three saved reports and hashes.

But the existing `MIC PCM` module runs a busy foreground loop that consumes the free-running DMA1 CH1's **~5.4–5.8 MB/s** raw GPIOB samples. Its 1KiB circular buffer is replaced every **~175–190µs**; codec computation or RFIC SPI operations while foreground-polled would lose raw samples. Past physical measurements proved TIM17's DMA requests cannot pace this channel: even with TIM17 disabled it runs flat out. Therefore merely lowering TIM17's ARR/PSC is not a solution.

## Implementation: DMA half-transfer IRQ producer

`c/k6/mod/sotto_gp0_irq_capture.h` implements a generic opt-in **DMA1 CH1, IRQ9** producer. DMA reads GPIOB IDR into a **1,024-byte ring** and fires its half/full interrupts for **512-byte halves**, roughly every **90–95µs**. Each ISR consumes exactly one completed half, sums eight groups of 64 raw PB1 samples and calls the *already physically demonstrated* fixed-point `gp0_pcm_push()` decimator for 8kHz samples. The produced signed PCM feeds the unchanged `sl_pcm_t` four-block (4×80×int16) Sotto queue via `sl_pcm_sample()`.

The handler executes in the K6's SRAM module (PIC), and never calls RFIC, UART, LCD, Sotto codec, flash or a firmware API. It acks the DMA flag immediately, refuses unexpected sequence/both HT+TC pending, checks a DMA error, counts missed halves and latches PCM queue overflow. An ISR exceeding **9,000 TIM17 counter ticks at nominal 96MHz** aborts rather than claiming unbroken PCM; interrupt CPU budget is about 8.9k ticks between 512-byte half transfers at 5.5MS/s. A foreground encoder may preemptibly run between IRQs, but it must consume a 10ms block fast enough to stay within 40ms of total PCM buffering.

Because K6 RAM modules cannot carry `.bss`/writable static data (PIC loader runs the module at arbitrary addresses), the IRQ context pointer is stored in a **four-byte writable literal inside the module's own SRAM code**. The Thumb interrupt trampoline uses PC-relative `adr`/`ldr` and branches to a C body; dedicated PC-relative assembly routines store/load that pointer, avoiding forbidden `R_ARM_ABS32` relocations. The complete module is compiled and *linked twice* at 0 and 0x1000, proving the binary stays identical. The handler/vector address is validated by separate Cortex-M0 Unicorn execution. It only arms after a PA-muted `tx_on` lease, once txdog has mapped the 48-vector SRAM page at address0; it checks the alias of the DMA IRQ9 vector at `0x20000064`, confirms DMA1 channel1 and NVIC IRQ9 are unused, installs the handler with global interrupts temporarily masked, and restores the **saved original vector, priority, NVIC pending state and prior PRIMASK** before leaving. No base firmware change or persistent RAM0 vector modification.

`c/k6/mod/sotto_mic_gp0_irq.c` is a **separate** signed loadable `MIC IRQ` diagnostic (not `SOTTO MIC`). It uses the existing API30 `mic_stream(7)` RFIC-only mic ADC route and GP0 selector12, waits 800ms for mic bias, calibrates the raw bit rate using the already-proven busy DMA path, then starts the new preemptible IRQ path. TIM17's cycle counter is explicitly **restarted after calibration** so the ISR cycle budget is real, not a false zero. The TX lease lasts at most 2.5 seconds with the RF PA muted. A bounded 600ms run drains 80-sample PCM blocks in the foreground and deliberately spends about **2ms per 10ms block** doing no foreground work (mocking the effect of codec load while allowing interrupts). The module stops/disarms DMA IRQ **before** accessing RFIC data and fully restores GPIO, TIM17, RCC, the mic route and RX/TX state. It has no FSK/RF modulation and leaves the original transmitter in slot30 unchanged.

It stores a boot-local CRC-protected 33-byte `MAB1` **v10** success mailbox: measured GP0 64:1 density rate, RFIC VOX, total 8kHz PCM samples, DMA half-IRQ count, blocks consumed, peak/average measured ISR timer ticks, clipped samples, missed halves, peak buffered blocks and PCM peak/RMS. On a failure it stores a **v11** report with specific failure code (`-26` vector/remap ownership, `-27` ISR/producer error, `-28` overflow, `-29` incomplete IRQ/PCM progress; existing -5/-6/-9/-11/-12/-17... errors are preserved). The result parser is `tools/k6_mic_result.py`, retrievable by serial command 0x86 after the user reconnects UART **without rebooting**.

## Validation completed offline

- PIC-compiles `MIC IRQ` for Thumbv6m, module code **3,771 bytes** after TIM17 counter fix. The previous `MIC PCM` and `MIC GP0` compile with exactly the same previous code sizes, **2,966** and **2,244** bytes.
- Real C native host model executes 1kHz, 2kHz and generator OFF input for a ~600ms continuous ISR run, preempting a simulated foreground, confirms 35+ PCM blocks, zero queue loss, exact radio teardown. Rejects a UART-connected microphone, old firmware without RFIC mic-only service, unarmed SRAM vector mapping and overlong foreground stall. Aborts return checksum-protected version11 evidence instead of a false success.
- Independent `sotto_gp0_irq_capture_host.c` drives **11,000 DMA half interrupts** through the real producer with foreground contention, checks exact seq, refusal of both flags/missed halves, PCM sample count ~8k, complete vector/NVIC restoration and overflow detection after an intentionally unserviced 75ms PCM queue.
- `tools/k6_sotto_gp0_irq_arm_gate.py` builds the **real compiled Cortex-M0 PIC module**, links at the exact tested offset and executes the actual IRQ9 Thumb trampoline and C handler under Unicorn with SRAM, DMA, TIM17, actual GPIOB PDM bytes and error injection. A normal half-buffer handler ran **1,238 ARM instructions** and returned cleanly with the right context pointer and PCM state; a deliberately combined HT+TC error ran 41 instructions and disabled the DMA channel. These are *instruction counts*, not measured K6 cycles (Cortex-M0 peripheral wait states and DMA arbitration aren't simulated). A physical ISR peak time below ~8.9k nominal counter ticks is the required gate before live encoder/RF integration.
- Focused tests on GP0 IRQ, PIC, GP0 PCM, old physical diagnostics and mailbox formats: **44 passed**. No physical IRQ measurement or Sotto encoding yet.

## Physical installation and attended next test

Prerequisites: radio idle, no RF output, dummy load connected, 7V+ battery. Current base firmware build ID should remain `b6a3df0d360d3110`. Slot25 should be the existing `MIC PCM` signed image SHA256 `91149cddfba6a50ace120fc9aab6cf406702d64ef563d4a7503f5495f6be6eb8`, and slot30 `SOTTO MIC` should be the unchanged envelope SHA256 `791de524e9640967f047f855d92e107405dbe765814f372b6003da5b375386a0`. Back up exact slot25 bytes before replacing **only slot25** with signed `MIC IRQ`; preserve earlier full 2MiB SPI backup `gp0-dma-v1/preflash-spi-mic-gp0-20261125T2041Z.bin`. Verify both slot25 readback and unchanged slot30 afterward. The regular cable-attached `run-coop 25` must return -4 (cannot use mic while serial owns jack), with TX false. No base flash is needed.

For physical ISR timing and PCM continuity: with K6 on dummy load, unplug programming cable, connect the existing signal generator to microphone input, set generator awake **1kHz / 0.1V output ON**, select `CATALOG → MIC IRQ`, hold physical PTT for ~2 seconds until `MIC IRQ SAVED` or `MIC IRQ ABORT`. Reconnect UART **without power cycle** and retrieve `mic-result` into `~/flux-bench/sotto-mic-calibration-20261125/gp0-irq-v1/tone-2khz-01.json`. An actual valid v10 with ~4.8k PCM, >3.5k DMA IRQs, 35+ consumed 80-sample blocks, peak ISR less than 9k ticks, low backlog, VOX >400, zero lost halves and clipping would support feasibility of continuous IRQ capture. An abort is equally valuable evidence of the bottleneck. No RF is sent by this diagnostic.

Only after that physical pass: embed this interrupt producer into a **separate** Sotto GP0 TX test module, stage on an isolated catalog slot after a space/readback check, and run the existing real Cortex-M0 Sotto encoder/FSK/decoder virtual-link gates before any attended over-the-air or dummy-load RF trial. The installed slot30 is the working *RF packet but PA11-silent* version, and must not be replaced until the new GP0 version is proven end-to-end.

## Physical installation and safe serial preflight completed (2026-11-25 21:19 UTC)

**Committed implementation:** `1d877067`, branch `feature/sotto-mic-continuous-pa-20261125`; focused tests 44 passing, and direct Cortex-M0 Unicorn IRQ trampoline tests passed both normal half-buffer and simultaneous HT+TC failure. The linked Thumb module code is **3771 bytes**, PIC entry offset 16; its SRAM IRQ trampoline/handler offsets in this build are 1832/1864, respectively. The average count of one *simulated* successful interrupt is 1,238 ARM instructions (not hardware cycles). The target's base firmware remained unchanged, and there was no transmission.

Reserved bench owner `flux-gp0-irq-install` for catalog-only update. Preflight read physical K6 at **7.77 V**, standby idle, TX=false, base build ID **`b6a3df0d360d3110`**. Exact pre-update **slot25 `MIC PCM`** envelope SHA256 **`91149cddfba6a50ace120fc9aab6cf406702d64ef563d4a7503f5495f6be6eb8`** was verified, with no premature overwrite. Original **slot30 `SOTTO MIC`** envelope SHA256 **`791de524e9640967f047f855d92e107405dbe765814f372b6003da5b375386a0`** verified before and after. Earlier *full 2MiB SPI backup* is preserved at `gp0-dma-v1/preflash-spi-mic-gp0-20261125T2041Z.bin`, SHA256 `5da971007f1c5ffe88f7131a8a2b58e5c1e76f669de0e1dad9aa8096b5827774`; no factory calibration altered.

Installed only **slot25 `MIC IRQ`**, code 3771 bytes, serial **`1795641552`**, envelope 3795 bytes and SHA256 **`b5068636d12758e9a31531f684f8f84abb3c3400a62a3f227e697c4b44ff8688`**. The radio's exact readback equals the signed image. Staged files and reporting are under `/home/tyler/flux-bench/sotto-mic-calibration-20261125/gp0-irq-v1/`, including `slot25-mic-irq.k6m`, `slot25-mic-pcm-before.k6m`, and `install-report.json`. The original `MIC PCM` diagnostic is preserved and can be reinstalled without a firmware flash. Slot30 remains untouched.

A safe cable-attached `k6_live.py run-coop 25 --allow-higher-api` loaded the real **3771-byte** module, reported `MAB1:CLR` then correctly exited **-4** because live UART occupies the microphone/PTT jack. This does not activate the GP0 IRQ, key PA or test physical ISR timing. Subsequent `k6_live.py status` reported standby idle/TX=false at **7.77 V** and mailbox `available=false`. Independent `k6_install.py status` shows base ID and exact expected slots. Persisted preflight in `gp0-irq-v1/cable-preflight.json`, `postflight-status.json`, `mailbox-preflight.json`. The session has not run a physical IRQ test yet.

### Next attended test, requiring operator PTT

1. Keep dummy load attached; unplug programming UART, attach external mic generator (or test headset). Leave the generator at its most recent **2kHz, nominal 0.1V, output ON**, verify it is awake. This uses the strongest previously measured injected tone and avoids changing the fixture.
2. Launch `CATALOG → MIC IRQ` (**slot25**; not `SOTTO MIC` slot30). Hold real PTT **at least ~2 seconds**, enough for ~800ms bias settling and ~600ms continuous interrupt capture; expect `MIC IRQ SAVED` or `MIC IRQ ABORT`.
3. Reconnect programming UART **without rebooting**. Retrieve `tools/k6_live.py --port /dev/ttyUSB0 mic-result --save ~/flux-bench/sotto-mic-calibration-20261125/gp0-irq-v1/tone-2khz-01.json`. A success should report >35 PCM blocks, >3500 DMA half-IRQs, peak TIM17 IRQ cost <9k counts, zero missed halves and PCM overflows and RFIC VOX substantially above 169 (OFF). On v11 error, use error code plus ISR peak/count for exact triage.
4. **No need to enable RF** in this step; it only proves IRQ capture can run concurrently with a mocked ~2ms per 10ms foreground workload. If the real ISR passes, proceed to separate Sotto GP0 TX module offline and emulator tests, then an attended SDR-monitored RF session. Be careful: the existing Sotto encoder reads SPI codebooks; whether SPI critical sections allow the ~90µs DMA IRQ deadline is still unproven. Never silently claim speech success from this probe.

## First physical continuous GP0 DMA IRQ capture — **PASS**, 2026-11-25

Operator ran the installed `MIC IRQ` slot25 diagnostic after disconnecting UART, with generator at **2 kHz nominal 0.1 V output ON**, K6 on dummy load, physically holding PTT. On serial reconnection the live K6 reported **transmitting=false, 7.76V, standby_idle=false**; an initial attempt to read the boot-local mailbox returned `BUSY`. A passive LCD screenshot, stored in `gp0-irq-v1/after-attended-test.png`, showed the radio had returned to its **CATALOG 12/17** list with `MIC IRQ` selected. The firmware only permits `MIC_RESULT` (0x86) while its VFO is at standby, so the menu was blocking the mailbox read, **not a stuck transmitter**. Sent one safe virtual `EXIT` UI gesture; returned to standby with no reboot or radio/RF reconfiguration and successfully retrieved the existing result.

The result is CRC-valid **MAB1 version10** / `gp0_dma_irq_8khz_pcm_stream`, saved at `/home/tyler/flux-bench/sotto-mic-calibration-20261125/gp0-irq-v1/tone-2khz-01.json`, SHA256 **`158dcc4928f3eb8b2e41c171275828b93c54adc5d05454bc5ef8c0cf37363259`**. Serial retrieval at **`2026-11-25T21:29:07.953786+00:00`** is not the hardware capture timestamp. A final live status showed standby idle, TX=false and battery 7.77 V, VFO A 443.500 MHz unchanged.

| Physical ISR/PCM measurement | Recorded value |
|---|---:|
| GP0 selector | 12 (PB1) |
| measured 64:1 bit-density stream | **84,810 samples/s** |
| continuously produced 8kHz PCM samples | **5,150** |
| consumed 8kHz samples | **5,120** |
| full 80-sample PCM blocks consumed | **64** |
| DMA1 half-buffer interrupt count | **6,826** |
| peak ISR timer ticks (96MHz nominal) | **2,363** (24.61us) |
| average ISR timer ticks | **2,222** (23.15us) |
| estimated half-buffer deadline | **~94.3us** at recorded rate |
| half-buffer interrupts missed | **0** |
| PCM queue peak depth | **1/4 blocks** |
| clipping | **0 PCM samples** |
| IRQ error code | **0** |
| PCM absolute peak | **16,632** |
| PCM RMS approximately | **8,832** |
| RFIC VOX peak | **437**, comfortably above generator-OFF ~169 |

**Physical result:** the newly implemented real *module SRAM* Thumb IRQ9 DMA producer remains active while the foreground intentionally spends ~2ms per 10ms PCM block not consuming audio. The ~24.6us longest observed ISR is well below the ~94us physical DMA half period, and the four-block PCM ring never backed up by more than one block. The RFIC microphone gate is active as expected, and no RF PA was enabled. This validates the *continuous capture/mocked-concurrency* feasibility gate, stronger than the previous short foreground-busy PCM captures. It is not yet proof of real codec/FSK concurrency: actual Sotto codebook SPI read timing and critical sections could delay IRQs, and the sampled 2kHz signal's spectral content was not recorded in this version10 report (RMS/peak/VOX only). Do not claim intelligible digital voice or transmit a new Sotto module until the GP0 encoder runs in an isolated virtual qualification and passes a physical attended test.

**Next engineering work:** integrate the `gp0_irq_audio_t` service into a **separate** Sotto GP0 TX source with common `sl_engine_t.mic` PCM ring and real Sotto codec resource API. Measure *true* module flash and scratch RAM budget (currently `SOTTO MIC` slot30 code ~9780B; new PIC IRQ component ~1000B, GP0 control and DSP further), ensure layout fits module arena without code/scratch collision. Also bound `sl_engine_tick()` + codebook SPI masking and ensure the encoder never holds PRIMASK globally for >~90us. Virtual Cortex-M0 RF IQ/FSK decoding and independent Sotto audio decode must pass before touching slot30 or making attended transmissions. A new diagnostic slot27 may be used only after verifying it is free/unmodified; preserve known-good slot25 and slot30.

## First combined Sotto encoder/GP0 IRQ size gate — **does not fit**, offline only

After the physical IRQ PASS, a **throwaway build-only source sketch** spliced the GP0 IRQ service into the actual 2026-11-25 pinned slim Sotto TX source (`sotto_live_tx.c`) and reused the exact Sotto codec adaptation flags from `tools/k6_sotto_module.py`. The target was linked twice through `modem.k6mod.build`, the same PIC check that produces installed radio images. This test did not enable RF or modify any installed modules. The preliminary sketch used a fixed **unvalidated** input rate and was **deleted after its sizing result** rather than leaving unsafe, runnable code in `c/k6/mod`.

| Physical firmware / compiled code budget | Bytes |
|---|---:|
| Physical K6 module arena, expanded configuration | **14,976** |
| Original installed `SOTTO MIC` code size | **9,780** |
| Original scratch maximum after 24B envelope | **5,172** |
| *Actual adapted* Sotto encoder context (`slim_enc_bytes`, host sizeof) | **3,160** |
| Sotto existing live engine (`sl_engine_t`) | **928** |
| Old engine + adapted encoder state | **4,088** |
| Remaining original scratch beyond required state | **1,084** |
| Working GP0 IRQ DMA ring | **1,024** |
| Working GP0 IRQ context | **72** |
| Actual combined source prototype PIC code | **10,960** |
| Combined-source available scratch, after envelope | **3,992** |
| Combined-source state + ring + context + alignment | **5,188** |
| **Combined-source scratch deficit** | **1,196** |

This **1.2KiB arena shortfall** is a genuine implementation gate, not something that further generator or RF tests will solve. A simple append of the real GP0 IRQ code to the 9,780B Sotto module is **not ready for installation**. Further work should reclaim at least ~1.2KiB of combined code/state space *plus additional buffer and stack headroom*, while retaining the physically proven 1,024B DMA ring and 40ms 4-block PCM queue if possible. A direct optimization may move some GP0 IRQ/control logic from the module into a firmware resident callable service (base image currently only ~136B flash headroom, so that needs flash reclamation), relocate cold Sotto codec routines into existing system SPI code pages, or significantly reduce the codec's 3,160B working set and/or transmit session state. Simply halving DMA or PCM buffers is a possible memory experiment but **reduces interrupt/service deadlines**, so must be independently measured against RFIC SPI masking; do not silently trade sample loss for fit. A proper next step is a **measured code+arena optimization branch** with failed-space check, compiled Pico/Unicorn codec/FSK gate and explicit worst-case interrupt deadlines before another physical install.
