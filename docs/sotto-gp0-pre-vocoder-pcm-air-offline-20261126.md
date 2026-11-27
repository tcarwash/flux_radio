# K6 GP0 pre-vocoder PCM over guarded FSK — staged offline experiment

2026-11-26 Pacific. Branch `experiment/sotto-gp0-raw-pcm-fsk-20261126` based on verified GP0 firmware research `2ddd7fc5`. **No on-radio flash, slot write, transmission, PTT automation, or RTL-SDR USB access has been performed for this diagnostic.** The known-good **118,408-byte** base MCU firmware, signed `SOTTO GP0` slot24 serial `1795751383` and preserved 2MiB SPI backup remain unchanged.

## Purpose

We have real received/decoded WAVs for generator 250, 500, 1k and 2kHz at nominal 0.1Vpp, plus generator-off. The reconstructed 1kHz signal has a strong ~191.2Hz vocoder harmonic comb and a 5.7dB level loss relative to 500Hz; we have reproduced that comb entirely in the native pinned Sotto encoder given a clean **956Hz** digital input. The physical generator is trusted within a few hertz. **We do not know whether the actual K6 8kHz PCM delivered to the vocoder already has the ~4% pitch shift, or whether the vocoder and the RF/decoder chain are responsible.** The generator-off received WAV has roughly −24.5dBFS broadband noise and is not an ADC noise measurement.

A byte-exact **pre-Sotto PCM capture** is the missing localization experiment, and should be completed before risking another GP0 anti-alias filter flash. We deliberately do **not** add MCU RAM0 buffers or firmware service callbacks, since AA3 left only ~12B RAM0 headroom and caused a physical queue-full abort.

## Diagnostic design and current offline results

`c/k6/mod/sotto_gp0_pcm_air_tx.c` is a **separate loadable API30 module** named `GP0 PCM AIR`. It reuses the *currently installed* firmware-owned GP0/PB1 DMA interrupt service:

1. Strict B-chip, existing API30 capability, exact **443.500MHz** tuned VFO, and **60ms uninterrupted physical PTT** before requesting the normal 25-second guarded TX lease. No serial cable can be connected during MIC/PTT because it shares the microphone jack. Dummy load is mandatory.
2. Enable real RFIC microphone route (`mic_stream(7)`), settle for **800ms** with 20ms PTT/cancel checks, then supply a 644-byte 4×80 PCM ring to `mic_stream(8,ring,87260)`. The code copies exactly **20 blocks = 1,600 signed16 samples**, nominal 8kHz / **200ms** pre-vocoder, in 3,200B of module scratch. Copies are foreground-only; the protected 1KiB raw DMA ring remains firmware-owned. No Sotto encoder is involved.
3. Record elapsed physical capture time from the existing boot clock `ticks()`, in **100μs units**. Stop the MIC/DMA producer and verify the host stop return *before starting any FSK*. Do not read/write SPI tables or transmit from the ISR.
4. Pack 67 L64 guarded packets, each 64-byte payload with D9 magic, PN9 whitening, CRC16/CCITT-FALSE and 0xA55A sacrificial guard. The 48-byte payload carries 24 **raw signed16 PCM** samples; the final packet is explicitly zero-padded. Every packet carries 4-byte per-session token, seq0–66, fixed sample count/rate, and measured 100μs capture duration.
5. Transmit via **existing guarded cooperative FSK** `fsk_async(0,words,33)` followed by `fsk_async(1)` until RFIC completion. The 33-word scratch buffer stays immutable until completed. Enforce 950ms/packet and 24sec overall watchdogs. **Require physical PTT throughout the complete approximately 11–13 second transfer**; release/cancel/deadline/fault stops FSK and calls `tx_off`. Radio completion only establishes local FIFO completion, NOT reliable SDR reception.

`tools/k6_gp0_pcm_air.py` verifies PN9, CRC, 0/2-byte slip, exact D9 header/metadata, all 67 unique packets, duplicate conflicts, session identity and exactly 3,200 bytes before creating **new** 8kHz mono s16le WAV and JSON. Missing packets are an explicit failure, never synthetic silence. Elapsed capture duration is included so we can estimate the PCM producer's actual cadence; it is not itself a direct GPIO PDM clock readout.

`tools/k6_gp0_pcm_air_rx.py` is a standalone, read-only RTL-SDR collector, centered on 443.5MHz with a 125kHz hardware IF offset and a separate *operator-verified* fine tuning correction (prior -31.3kHz is a historical setting). The receiver records the post-tune 128k complex64 IQ, then applies the existing blind preamble/sync/FIFO decoder and the D9 parser. A bounded 4–35s capture, no serial or radio control, new files only. A standalone RTL read must run **instead of** the normal Sotto Monitor (both use the same USB RTL device). Failure preserves IQ for offline retune/recovery. The read path uses bounded chunk conversions to prevent whole-file complex128 temporary allocations; SDR USB synchronous reads can still block on a failed device.

**Passed offline:**
- Actual new C module host-executed with bounded mock DMA/PCM and FSK callbacks, confirms precise **1,600-sample byte-identical roundtrip**, 67 sequential guarded packets, no cable/PTT bypass, no active TX on return, RF service failures fail closed.
- Extra cases for cancellation, invalid tuned VFO, lost/stalled PCM, channel failures, mic stop failure, **physical PTT release during FSK** and malformed/reordered/duplicated/missing packets.
- Actual ARMv6-M PIC build of diagnostic code with scratch and 1KiB protected end-of-arena reserve checked.
- Full synthetic physical PHY: 67 C-generated physical FSK packets modulated to an 11.144-second complex I/Q waveform; the real independent blind synchronization/demodulator recovered **67/67 CRC-valid frames**, exact PCM SHA256 `06d921ab2d19d52005b189ac50c6a3cf607b9297abf52a38c3a53ff7ec47dddf`. This does **not** prove physical RF reception.
- Read-only Antigravity Hydrogen Medium second opinion, job `8f432c427015`, found no static blocker for **offline staging**, flagged memory and PTT safety problems fixed in subsequent revisions. The worker did not execute tests or flash a radio. Independent host tests were run by coordinating agent.

## Intended attended hardware protocol — NOT EXECUTED

This experiment does not justify a base MCU flash. The old signed voice module in slot24 must remain untouched.

1. With radio **idle**, temporarily reconnect programming cable, inspect installed library slots/readback and verify an actually *free and unreserved* slot. Do not guess slot25, since it hosted earlier MIC diagnostics. Back up any slot before replacement and preserve original image/hash. Sign this **new diagnostic module** only through the existing trusted Flux signing/install process; verify signed code, fresh monotonically valid serial and exact SPI readback. Never expose module signing keys or run a mass flash.
2. With user-attended dummy-load configuration and external mic input active, disconnect programming cable. Exact VFO 443.500MHz and no other transmitters on the bench. Stop existing Sotto Monitor before opening RTL-SDR. Open the new **RX-only** collector and verify fine tune correction with current receiver evidence, not hardcoded blindly:
   `python tools/k6_gp0_pcm_air_rx.py --seconds 19 --fine-offset-hz -31300 --output-prefix recovery/pcm_air/<NEW_RUN_NAME>`
   The example -31300 is *historical*; an adjusted observed correction may be required. The CLI does NOT install or run the K6 module.
3. Launch `CATALOG → GP0 PCM AIR` and deliberately hold physical PTT **until the module completes** (approximately 11–13 seconds, not just the 0.2s capture). It should dekey and restore RX on success/failure. If any watchdog/cancel/problem occurs, do not claim success; check LCD, radio RX state, and saved IQ. No unattended TX.
4. Run twice with unique file names: **generator OFF** (mic electrical connection unchanged, output off) and **500Hz or 1kHz at 0.1Vpp**. A third 1k/500 pair isolates the frequency response. The captures are only 200ms each but consist of *full-resolution, pre-vocoder signed PCM*.
5. Compare precode PCM vs previous Sotto Monitor WAV: tone frequency/PCM clock, RMS, peak, clipping counts, 300–3.9kHz PSD, and noise floor. Use precode PCM to simulate AA3/CIC3 and potential cheap IIR/FIR anti-alias filters against a **measured** rather than synthesized PDM noise model. A 200ms PCM capture cannot reveal high-rate PDM spectral alias origins already folded; do not claim that it uniquely identifies GP0/PDM noise source. If the precode PCM is clean but decoded Sotto noisy, investigate pitch/codec and RF before a new TX filter flash.

## Ready-to-run offline PCM OFF-versus-ON analysis

After two **complete**, CRC-verified captures (generator OFF and known tone ON, each with its own new .wav and .json files), use:

`python tools/k6_gp0_pcm_compare.py --off-wav OFF.wav --off-json OFF.json --on-wav ON.wav --on-json ON.json --nominal-tone-hz 500 --json-out comparison-500-new.json`

This command is **read-only on all source files**, writes only a fresh JSON result, and refuses altered/mismatched WAV hashes, invalid sample counts, invalid capture time, or reuse of the same session token. It fits a sine wave to the 1,600-sample PCM record, estimates its dominant component and residual, compares tone RMS against the independent OFF noise RMS, and reports the **actual produced PCM samples per MCU-clock wall second** using 100µs timing ticks. It also calculates the physical generator frequency inferred from the combination of decoded raw PCM frequency and measured PCM sample cadence. This inference is conditional on the MCU clock being accurate, and the short 200ms window limits frequency/noise confidence. The result bypasses Sotto entirely.

The synthetic test suite now includes a deliberately **faster 8,300Hz physical PCM clock** with a nominal physical **500Hz sine** that appears near **482Hz when labeled as 8,000Hz**. The analysis recovers approximately **500Hz** after using the physical capture tick count, guarding the important inverse clock/pitch relationship. Run:

`python -m pytest -q tests/test_k6_gp0_pcm_air.py tests/test_k6_gp0_pcm_air_rx.py tests/test_k6_gp0_pcm_compare.py`

This analysis also complements—but is not a substitute for—the original 87,260Hz GP0/64bit-density hardware calibration: it directly measures the *end of the 8kHz producer* and cannot recover which higher-frequency PDM aliases already folded into 8kHz.

## Next qualitative on-air improvement candidate

A **separate, default-bypass Sotto Monitor browser-only receiver clarity filter** has been developed offline in `/storage5/dev/sotto-monitor-rx-clarity-20261126` (branch `experiment/sotto-rx-clarity-filter-20261126`). A 4th-order Butterworth 1.6kHz voice-focus option suppressed decoded **generator-OFF noise by ~6.21dB** in a 48kHz-causal offline approximation, while attenuating speech-plus-noise by ~3.84dB; a less aggressive 2.2kHz option suppressed OFF noise by ~4.0dB. This is a reversible *listening* improvement candidate for a single later live voice A/B, **not** a demonstrated TX fidelity improvement. Preserve the raw original WAV, test Bypass vs Balanced vs Voice focus with the same RF packet stream and listen for consonants/intelligibility.

**Go/no-go:** do not replace known-good Sotto module or flash AA3/CIC3 based on synthetic alias tests alone. Require real precode PCM, ISR/queue stability and measured audio benefit. No site-specific signing credentials, SPI dump, IQ or audio bytes are committed to the public repository.
