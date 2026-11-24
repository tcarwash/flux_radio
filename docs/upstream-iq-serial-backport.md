# Flux: IQ streaming and high-speed serial — upstream backport guide

Source: [Flux](https://github.com/tcarwash/flux_radio); intended upstream: [Blowfang](https://github.com/baitisj/blowfang).

## Overview

Flux demonstrates software-derived receive I/Q on the FD6818B K6 using GP0/PB1 and the existing programming cable, without hardware modification. Three largely independent changes are candidates for upstream: high-speed asymmetric UART transport, continuous IF/IQ acquisition, and non-rebooting live serial control.

## 1. Physical IQ results

Tested on an FD6818B K6 with a Prolific PL2303 programming cable.

| Mode | UART baud | Complex samples/s | Result |
|---|---:|---:|---|
| int8 IQ, CIC ratio 32 | 115200 | 2,900 | Pass (3 s) |
| int8 IQ, ratio 8 | 500000 | 11,565 | Pass (three 10 s captures) |
| packed signed 6-bit IQ, ratio 4 | 500000 | ~22,000 | Pass (10 s captures) |
| int8 IQ, ratio 4 | 550000 | ~23,000 | Nine output frames dropped in 3 s |
| int8 IQ, ratio 8 | 115200 | ~11,600 | 113 output frames dropped in 2 s |

The three successful ratio-8 runs delivered 346,962 complex samples total, with no detected DMA overruns, uncertain DMA laps, DMA transfer errors, UART errors, response CRC errors, output drops, sample-index gaps or nonmonotonic timestamps. Maximum polling pause was about 177 microseconds; at most ~1.1 KiB was pending in the 8 KiB ring. Packed 6-bit operation was subsequently qualified on both Base-only and four-bundle firmware. A raw 2048-count snapshot measured ~89.5 kcounts/s.

## 2. Acquisition and DSP

```text
FD6818B GP0 -> MCU PB1 -> GPIOB IDR DMA (8 KiB circular ring)
 -> population counts (64 observations/count)
 -> time-aware quadrature mixer -> third-order complex CIC
 -> int8 or packed signed 6-bit I/Q -> framed UART TX interrupt queue
 -> host capture and DSP
```

DMA channel 1 and TIM17 are reserved only when unused. TIM17 does not actually establish a precise DMA sampling cadence: approximately 5.9 million DMA GPIO observations/s are repeated GPIO reads, NOT a demonstrated RF ADC conversion rate. CPU and bus activity affect timing. Preserve measured rates and source timestamps instead of assuming a fixed sample clock.

The portable mixer uses a 32-bit phase accumulator and 16-entry Q7 cosine table. The physical service uses measured-time phase estimates and retains five fractional bits through the CIC. The third-order complex CIC uses modular 32-bit integrators, three comb stages and power-of-two ratios through 32. Its initial Cortex-M0 object is ~292 bytes; initial portable mixer ~144 bytes. Neither size includes the full acquisition service.

UART transmission is interrupt-driven, with eight independent framed buffers in IQ mode (two in raw mode), short critical sections for publication and explicit queue-drop accounting. At ratio 4 / 550000 baud, switching from polling to interrupts reduced lost frames from 25 to nine; packing six-bit components made ratio 4 / 500000 practical.

The host writes JSON statistics and NPZ samples/frame metadata. It detects source-index and sequence gaps; spectral estimates are suppressed if missing samples would make a concatenated FFT misleading.

## 3. Physical IQ wire extension

Uses existing A6 framing: command, big-endian address and length, payload, CRC16/XMODEM (initial zero). The legacy cable-session burst queues baud change, capture, and return to baseline at 115200 before switching.

- Request 0x70: frequency u32 LE, max milliseconds u32 LE, mixer Hz u32 LE, mode u8 (0 raw, 1 int8 IQ, 2 packed 6-bit IQ), ratio u8, two reserved zero bytes.
- Sample response 0x69: sequence in frame address. Payload: first source index u32 LE, end timestamp in 96 MHz cycles u32 LE, measured cycles per DMA observation Q8 u32 LE, mode u8, ratio u8, two reserved zero bytes, up to 240 data bytes.
- Final 0x70: 24 u32 LE statistics followed by receive frequency, mixer frequency, RSSI register, IF selector, bandwidth register and GP0 route readback. See host parser for exact names and signed interpretation.
- Raw source stride is 64; IQ stride is 64 * ratio.
- int8 mode fits 120 complex samples/frame; packed mode fits 160. Pack signed six-bit I then Q, LSB first, two pairs per three bytes; final odd pair occupies two bytes with zero padding. Host expands each signed component by four to int8 scale. This reduces payload by 25%.

IQ timestamps estimate the final contributing observation before CIC group-delay correction. Raw timestamps are reconstructed from measured rate after acquisition. The separate generic K6IQ/CRC32 format is a host/simulator prototype, not the physical A6 format.

## 4. UART speed enhancements

| Test | Baud | Application payload |
|---|---:|---:|
| Original 64-byte pipelined SPI | 115200 | 8.3 kB/s |
| Bidirectional SPI | 192000 | 12.4 kB/s |
| Binary pattern downlink | 500000 | 42.4 kB/s |
| SPI downlink | 500000 | 40.0 kB/s |
| Binary pattern downlink | 550000 | 46.3 kB/s |
| SPI downlink | 550000 | 43.4 kB/s |

550000 was the fastest clean tested downlink (actual divisor-rounded 548571); 560000 lost frames and tested rates above that failed. Prefer 500000 for margin. Sustained uplink at 230400 showed noise errors and 256000 also framing errors, without hardware overrun or ring drops. 192000 passed the full bidirectional test. These results are specific to the tested cable/radio/host.

The key is asymmetric transfer: queue all three legacy commands (BAUD FAST, CAPTURE/STREAM, BAUD 115200) at baseline. Acknowledge first switch at baseline, wait 30 ms, send fast downlink, acknowledge return at fast speed, guard 30 ms and restore baseline. Host drains its outbound commands before switching. Legacy idle recovery restores 115200 after five seconds; reset also restores baseline.

Other changes: nibble-table CRC16, host binascii.crc_hqx, direct CRC over encoded response with no extra payload copy, outgoing 256-byte frames, incoming 96-byte limit in the legacy lab, error counters (hardware overrun, framing, noise, software ring drop), bounded transfers and explicit recovery. The legacy lab commands are 0x62 baud, 0x54 echo, 0x74 pattern, 0x64 SPI read, 0x71 UART counters.

## 5. Non-rebooting live serial

The legacy loader is non-returning and resets after END/timeout. The live path uses one bounded request per host-loop pass on a returning UI arena stack trampoline; normal control requests stay at 115200 and preserve the legacy handshake.

V1 request: ASCII FLXLV1 (6 bytes), opcode u8, sequence u8, argument LE32, CRC16/XMODEM BE over preceding 12 bytes. Response: A6, opcode, sequence zero-extended BE16, length BE16, payload, CRC16 BE excluding A6. First payload byte is result (0 success, 1 busy, 2 invalid, 3 SPI I/O error).

V2 uses FLXLV2 followed by a binary-safe encoded body and NUL. Raw body: cmd u8, seq u8, length LE16, arg LE32, payload, CRC BE16 (covers prefix and unencoded body excluding CRC). See live protocol guide for exact encoding.

Features include status, volatile VFO tuning, mode and VFO selection, live memory/library reads, full SPI backup, Hamlib NET rigctl, verified temporary RAM module execution, negotiated 192000 upload, persistent packed-library install/remove, and API-25 cooperative cancellation. LCD snapshots, virtual keys and leased PTT are later independent additions; not required for IQ.

Important hardware finding: PA10 is shared between UART RX and PTT. Instantaneous low sampling during frame-prefix wait caused legitimate uploads to abort. Require 10 ms continuously low during prefix wait, retaining power-off and frame/commit boundary guards. Physical tests reproduced and verified the fix. See commit 53c0b103ea.

Live bulk downlink uses negotiated bounded windows, automatic baud restoration, CRC/sequence/address checks, and verified read-only fallback. Original 0x6b supports up to 4 KiB at fast rate. Extended 0x7d supports up to 32 KiB at 500000; 0x6c carries address plus 256 bytes, 0x6d completes with counts and error deltas. No per-frame ACK; host remains silent until recovery. Full physical 2 MiB backup:

| Path | Time |
|---|---:|
| Original live 4 KiB windows | 102.004 s |
| Extended live 32 KiB windows | 60.704 s |
| Legacy fast loader (rebooting) | 53 s |

Extended live delivers 33.74 KiB/s, 1.68x previous live full backup. A 32 KiB window still occupies the foreground scheduler for roughly 700 ms. Non-rebooting does not mean fully nonblocking.

Live fast upload is stop-and-wait at 192000 with 128-byte chunks, deterministic probe, CRC and UART counters, automatic baseline recovery, separate READY and COMMIT. No automatic retry of mutations or execution. Physical acceptance included temporary module execution, exact 7168-byte persistent module replacement and readback, removal, retained original logical objects/calibration/VFOs, and Hamlib CAT.

## 6. Source and commit map

| File | Responsibility |
|---|---|
| [c/k6/serial_if_lab.c](https://github.com/tcarwash/flux_radio/blob/main/c/k6/serial_if_lab.c) | Physical acquisition, DMA, mixing and streaming |
| [c/k6/if_iq.c](https://github.com/tcarwash/flux_radio/blob/main/c/k6/if_iq.c) | Portable fixed-point mixer |
| [c/k6/if_iq.h](https://github.com/tcarwash/flux_radio/blob/main/c/k6/if_iq.h) | Mixer interface |
| [c/k6/if_cic.c](https://github.com/tcarwash/flux_radio/blob/main/c/k6/if_cic.c) | Complex CIC decimator |
| [c/k6/mod/wspriq.c](https://github.com/tcarwash/flux_radio/blob/main/c/k6/mod/wspriq.c) | Earlier narrowband implementation |
| [tools/k6_iq_capture.py](https://github.com/tcarwash/flux_radio/blob/main/tools/k6_iq_capture.py) | Host capture, validation and export |
| [firmware/k6-aura/live-serial.rs](https://github.com/tcarwash/flux_radio/blob/main/firmware/k6-aura/live-serial.rs) | Live protocol |
| [firmware/k6-aura/apply-live-serial.py](https://github.com/tcarwash/flux_radio/blob/main/firmware/k6-aura/apply-live-serial.py) | Live firmware integration |
| [firmware/k6-aura/apply-serial-lab.py](https://github.com/tcarwash/flux_radio/blob/main/firmware/k6-aura/apply-serial-lab.py) | Serial lab integration |
| [tools/k6_live.py](https://github.com/tcarwash/flux_radio/blob/main/tools/k6_live.py) | Live host client |
| [tools/k6_live_bench.py](https://github.com/tcarwash/flux_radio/blob/main/tools/k6_live_bench.py) | Live hardware acceptance |
| [tools/k6_serial_bench.py](https://github.com/tcarwash/flux_radio/blob/main/tools/k6_serial_bench.py) | Serial throughput benchmark |
| [docs/k6-serial-iq-lab.md](https://github.com/tcarwash/flux_radio/blob/main/docs/k6-serial-iq-lab.md) | IQ protocol and physical results |
| [docs/k6-serial-throughput.md](https://github.com/tcarwash/flux_radio/blob/main/docs/k6-serial-throughput.md) | UART benchmarks |
| [docs/k6-live-serial.md](https://github.com/tcarwash/flux_radio/blob/main/docs/k6-live-serial.md) | Live protocol and hardware acceptance |
| [docs/k6-live-serial-roadmap.md](https://github.com/tcarwash/flux_radio/blob/main/docs/k6-live-serial-roadmap.md) | Transport roadmap |
| [docs/k6-if-streaming-wspriq.md](https://github.com/tcarwash/flux_radio/blob/main/docs/k6-if-streaming-wspriq.md) | DSP background |

| Commit | Description |
|---|---|
| [1ca752b607](https://github.com/tcarwash/flux_radio/commit/1ca752b607) | Portable Cortex-M0 IF/IQ mixer |
| [c96bebffce](https://github.com/tcarwash/flux_radio/commit/c96bebffce) | Third-order CIC decimator and alias tests |
| [953af9bfd9](https://github.com/tcarwash/flux_radio/commit/953af9bfd9) | High-speed serial bursts and hardware benchmarks |
| [b0c0fa1ddb](https://github.com/tcarwash/flux_radio/commit/b0c0fa1ddb) | Emulator UART TX interrupt support |
| [1e802dc5e6](https://github.com/tcarwash/flux_radio/commit/1e802dc5e6) | Physical GP0 DMA and continuous IQ export |
| [be3debd8b3](https://github.com/tcarwash/flux_radio/commit/be3debd8b3) | Packed IQ and fast serial hardware qualification |
| [383afc1053](https://github.com/tcarwash/flux_radio/commit/383afc1053) | Experimental SPI IQ engine checkpoint |
| [986289133d](https://github.com/tcarwash/flux_radio/commit/986289133d) | Packed platform and expanded arena |
| [d3ddd6b104](https://github.com/tcarwash/flux_radio/commit/d3ddd6b104) | Initial non-rebooting live serial |
| [c9c6125ef4](https://github.com/tcarwash/flux_radio/commit/c9c6125ef4) | Capability-aware CAT and Hamlib |
| [7c6bfcbf98](https://github.com/tcarwash/flux_radio/commit/7c6bfcbf98) | Negotiated downloads and verified RAM modules |
| [53c0b103ea](https://github.com/tcarwash/flux_radio/commit/53c0b103ea) | Shared UART/PTT fix and physical acceptance |
| [66e512bd63](https://github.com/tcarwash/flux_radio/commit/66e512bd63) | 32 KiB live streaming windows |
| [71f17c8a49](https://github.com/tcarwash/flux_radio/commit/71f17c8a49) | Cooperative control and safety hardening |
| [b0d930a1ed](https://github.com/tcarwash/flux_radio/commit/b0d930a1ed) | LCD screenshots and virtual keys |
| [a5409ea420](https://github.com/tcarwash/flux_radio/commit/a5409ea420) | Remote LCD/keypad in normal live builds |
| [6e1366ebf2](https://github.com/tcarwash/flux_radio/commit/6e1366ebf2) | Generic timestamped K6IQ host framing |
| [e2436c51f4](https://github.com/tcarwash/flux_radio/commit/e2436c51f4) | Decoder-neutral DSP reference |
| [838feeb2a2](https://github.com/tcarwash/flux_radio/commit/838feeb2a2) | Measured-rate DMA simulation |

Use these as patch-extraction references rather than blindly cherry-picking. Some commits include Flux-specific packed storage and expanded arena dependencies.

## 7. Recommended backport order

1. UART infrastructure: baud divisors, CRC optimizations, counters, buffered TX.
2. High-speed binary downlink: baseline negotiation, 500 kbaud output, automatic recovery.
3. Portable mixer and CIC with native tests.
4. GP0 DMA acquisition, timing, interrupt-driven framing, packed six-bit mode.
5. Returning live dispatcher and capability discovery; status/tune and transport ownership.
6. Optional larger SPI windows, RAM execution, module management and cooperative cancellation.

RAM is the main integration constraint: the physical service needs an 8 KiB DMA ring plus transmit buffers, DSP state and stack. Later Flux live layouts have an 11,776-byte arena. Adopt equivalent workspace or reduce buffers and requalify. Flux Catalog, packed library, Hamlib and the full V2 protocol are not inherent prerequisites for IQ.

## 8. Reproduction

Use a matching IQ-capable Base pack and firmware; IQ is default for Base, Scan, Designer and Data combinations; OTA/Sotto excluded. Preserve rollback firmware and verified SPI backup before flashing. Keep physical access exclusive.

```sh
python3 tools/k6_iq_capture.py --port /dev/ttyUSB0 --ms 10000 \
  --ratio 8 --bits 8 --baud 500000 --hz 162550000 --mix 8000 \
  --output serial-test-output/iq8

python3 tools/k6_iq_capture.py --port /dev/ttyUSB0 --ms 10000 \
  --ratio 4 --bits 6 --baud 500000 --hz 162550000 --mix 8000 \
  --output serial-test-output/iq6

python3 tools/k6_serial_bench.py --port /dev/ttyUSB0 \
  --downlink-only --rates 500000 --blocks 1024 --passes 8 \
  --output serial-test-output/stream.json

python3 -m pytest -q tests/test_k6_if_iq_core.py \
  tests/test_k6_iq_capture.py tests/test_k6_iq_frontend.py \
  tests/test_k6_iq_stream.py tests/test_k6_serial_bench.py \
  tests/test_k6_live_control.py
```

## 9. Qualification boundaries

Hardware verified: 500 kbaud downlink, 192 kbaud bidirectional, bounded continuous 8-bit and packed 6-bit IQ with explicit loss detection, live CAT, SPI backup and RAM module lifecycle.

Not established: calibrated RF amplitude/phase or precise RF sample clock, full alias response, indefinite continuous streaming, simultaneous normal CAT during IQ capture, universal cable compatibility, arbitrary TX IQ injection, or on-air packet decode from this capture service. The IQ service is receive-only, bounded to ten seconds, and currently uses a cable session that restarts at session end. It is distinct from the later non-rebooting live dispatcher. Combining them is a future improvement.

For upstream, start with [953af9bfd9](https://github.com/tcarwash/flux_radio/commit/953af9bfd9), [1e802dc5e6](https://github.com/tcarwash/flux_radio/commit/1e802dc5e6), and [be3debd8b3](https://github.com/tcarwash/flux_radio/commit/be3debd8b3). For live serial start with [d3ddd6b104](https://github.com/tcarwash/flux_radio/commit/d3ddd6b104) and [7c6bfcbf98](https://github.com/tcarwash/flux_radio/commit/7c6bfcbf98).
