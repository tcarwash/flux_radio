# FD6818B extra-low power and serial TX laboratory

Date: 2026-11-19. Status: measured on one K6, experimental only; candidate for a future Base firmware feature. Primary goal: minimize unnecessary QRM during local digital-mode and RF experiments, not necessarily extend battery life.

## Hardware and method

- K6 with FD6818B (ID 0x4829), Flux cable-only Data + Scan serial-lab firmware, USB programming cable at 115200 baud.
- Antenna-to-antenna RTL-SDR capture, 433.750 MHz center, 1.024 MS/s, fixed tuner gain 0 dB. Nominal TX 433.500 MHz. Same arrangement across sweep; no calibrated coupler, power meter or frequency reference.
- Before each trial capture approximately one second of baseline; abort on a strong channel signal. Send one CRC-framed serial command with no automatic retries. Hardware FSK sends eight words at nominal 1200 baud: `4147 3753 5520 4653 4b20 55aa aa55 0123` (hex). Follow with AG7SU in 800 Hz Morse over FM at 20 WPM; return to RX, turn RF off, and end cable session.
- The initial direct TX returned success and produced a roughly 152 ms burst in the initial analysis window. Wider analysis found the CW carrier near 433.469625 MHz, about -30.375 kHz relative to the requested frequency. This is an apparent offset of the combined radios, not a calibrated transmitter error. Initial carrier-keyed ID was replaced by FM Morse; the subsequent reference capture shows the expected AG7SU tone sequence with about 2.6 kHz tone deviation.
- All 11 sweep commands returned modem completion; no SDR ADC clipping or capture errors were reported. Completion is not proof of correct remote packet reception. Absolute power, BER, harmonics, battery current and temperature were not measured.

## PA control

FD6818B driver register 0x36 high byte is the calibrated APC target; bit 7 enables PACTL and the remaining gain fields use a band-specific low byte (UHF 0xA2, VHF 0x88). PA-off uses 0x0022. Register 0x19 supplies a separate PA bias setting, left unchanged. Do not vary bias or driver gain until separately characterized. The factory calibration table in the SPI backup gives UHF 430 MHz high/mid/low bytes 250/160/180. The surprising mid/low order is a reason to measure actual power rather than assume every calibration value is monotonic across named settings.

The lab substitutes a RAM-only APC target `floor(factory_low * percent / 100)` using the existing PA setup path. No calibration flash write, and standard power modes remain unchanged. Firmware command 0x79 accepts little-endian frequency (u32) plus percentage (u8); only the configured experimental frequency is accepted. A nonnegative reply encodes factory low in bits 15:8 and applied target in bits 7:0; negative replies signal failure. This builds only with the explicit serial-lab configuration and Data service. Do not use the obsolete four-byte request from the initial TX experiment.

## Measured sweep

Median integrated RF power during FM ID in a 40 kHz window around the observed carrier minus median pre-TX noise, normalized to the factory low reference. Window uses 8192-sample Hann FFTs at 1.024 MS/s; baseline first 100 FFTs and ID blocks 200:530. SNR is integrated-window signal/noise, not demodulator Eb/N0. Near/below-noise estimates are floor-limited and not reliable power measurements.

| APC percent | APC byte | Relative RF dB | Integrated SNR dB |
|---:|---:|---:|---:|
| 100 | 180 | 0.0 | 30.5 |
| 75 | 135 | -2.3 | 28.3 |
| 50 | 90 | -4.4 | 26.1 |
| 45 | 81 | -6.7 | 23.7 |
| 40 | 72 | -23.7 | 6.8 |
| 35 | 63 | -33.0 | -2.4 |
| 30 | 54 | -39.3 | -8.8 |
| 25 | 45 | -40.9 | -10.4 |
| 10 | 18 | -42.2 | -11.8 |
| 5 | 9 | -40.7 | -10.2 |
| 0 | 0 | -41.5 | -11.0 |

The knee lies between 72 and 81. Target 81 is a conservative experimental reduced setting; 72 is a candidate micropower setting; 63 is not yet demonstrated to be reliably usable. If factory low really were 1 W, -23.7 dB would be 4.3 mW; this is illustrative only. At 0 APC, PACTL is still enabled: this is NOT a demonstrated PA-disabled exciter mode. Do not infer leakage output below the receiver noise floor. Repeat exact targets around the knee, at different temperatures, battery voltages and frequencies, across units; measure actual output with a calibrated load/coupler and test packet error rate and spectrum. No claim of clean emissions or guaranteed low power on other radios.

## Thermal and PA-health telemetry

- Existing `c/k6/mod/refcal.c` samples KD32F328 MCU internal temperature ADC channel 16 and VREFINT channel 17. It enables CCR TSEN/VREFEN, uses longest sample time, settles 200 us, accumulates 16 conversions per channel, and restores ADC selection/sample time/CCR. No known factory temperature calibration; temperature/reference ratio is initially a relative index, not degrees Celsius. This is source-verified, not measured in this TX experiment. Validate availability and monotonicity on this hardware before relying on it. Calibrate with a known ambient/contact sensor if useful.
- Existing radio API reads battery ADC channel 1 (PA1), and mic/VOX channel 0 (PA0). Battery voltage under load and sag can be useful warnings but do not measure PA current. VREFINT can help normalize ADC measurements.
- No verified direct PA junction temperature, RFIC temperature, PA supply current, forward/reflected RF power, SWR or APC feedback readback in the inspected firmware/board interface. The APC target is a command, not a power sensor. Check datasheet, PCB and unused ADC pins before claiming more telemetry.
- Add lab logging of raw temperature/reference and battery ADC before TX, immediately after and during cooldown; collect baseline temperature and repeatability. MCU temperature is a slow proxy and cannot protect the final against instantaneous mismatch or hot spots. Prefer a contact thermocouple or isolated temperature probe on/near the PA, plus supply current and directional forward/reflected power measurement on a rated 50-ohm load. Do not put conductive probes across RF circuitry.
- Reduced radiated output can still dissipate power internally; an APC knee may increase distortion or instability and reduced bias is not automatically safer. Keep trial bursts short, verify current, spectrum and temperature relative to normal low power; stop for unexpected heating/current, high reflected power or unstable emission. No thermal safety thresholds until characterized.
- **Important implementation gap:** the current lean image compiles `txdog::keyed`, `limit`, `poll` and `unkeyed` as no-ops. The direct lab path has bounded FSK polling and finite Morse loops plus explicit RX/PA shutdown, but no effective independent key-up watchdog demonstrated in this build. Before longer tests or Base integration implement and exercise an independent TX timeout/fail-safe, including reset/hang and lost-host cases. Do not mistake a watchdog function call for protection in this image.

## Future Base integration

1. Expose an optional Extra Low / Lab power profile alongside normal powers; use per-unit/per-band characterized APC targets, not fixed wattage labels or global percentages. Fall back to factory settings if profile is unavailable. Do not alter factory calibration.
2. Keep normal TX authorization, channel/band locks, runtime key-up limits and station identification. Prefer the lowest verified power that maintains the desired link margin. Base mode should not add arbitrary serial RF register access.
3. Add read-only thermal/voltage diagnostics with clear units and validity. Independent thermal protection requires validated PA-proximate sensor or other hardware instrumentation, not just MCU ADC.
4. Validate repeated packet reception/BER, actual RF output, occupied bandwidth, harmonics/spurious emissions, frequency accuracy, supply current, duty cycle, mismatch and thermal behavior. Test multiple units and both bands before declaring a supported mode. Use conducted tests where possible to meet the QRM objective.

## Reproduction and artifacts

Worktree `~/dev/flux-serial-throughput`, branch `serial-throughput`. Experimental patch `firmware/k6-aura/apply-serial-lab.py`; separate manually launched one-frame module `c/k6/mod/rffsklab.c` (slot 23 on test radio, earlier experiment, not the serial sweep path). Build with `K6_SERIAL_LAB=1 K6_FSK_RX_SERVICE=1 K6_SCAN_SERVICE=1` and isolated `K6_OUT`, `K6_IMAGE_NAME`. Current image: `serial-test-output/tx-power-build/flux-serial-tx-power.bin` (122716 bytes, SHA256 a8e320200affe5709d12a497cb2110151f48a4d54fa8f1d6784b8b6c298ddf87). Original SPI and firmware backups in `serial-test-output/`. Raw IQ and capture scripts preserved in ignored `serial-test-output/rf-power/` (copied from `/tmp/flux-rf`); captures are interleaved unsigned 8-bit I/Q in NumPy files. Host scripts are experimental, not release tooling. The module and direct serial command are not proposed as Base features verbatim.

## Second-stage matrix: exact APC, telemetry, variable FSK frames

The later matrix image (`serial-test-output/tx-matrix-build/flux-serial-tx-matrix.bin`, SHA256 `c69e514f5c07dd978a19a2bc9856c12349b44f277b5ad8018132163e0dede8f4`) uses Base + Data, Scan disabled to save flash. It supersedes the percentage protocol above: opcode 0x79 now takes exactly 8 bytes, `struct.pack('<IBHB', 433500000, apc_target, baudword, nwords)`. The word is nominally round(0x3065 * baud / 1200), max 0xffff; 1-64 words; absolute APC must not exceed factory low. Fixed frequency, original TX authorization and FM AG7SU ID retained. Seven signed LE int32 response fields: packed factory/actual APC or negative status, pre-TX temperature sum, pre-TX VREF sum, pre-TX battery raw, keyed battery raw, post-TX temperature sum, post-TX VREF sum. The sensor sums each represent 16 12-bit conversions. No temperature calibration or current measurement. Independent watchdog remains absent in this lean build.

TX setup writes 3f=8000, 58=00c1, 70=00e0, 72=baudword, 5c=5665, 5d=(2*nwords-1)<<8, 59=8028 then 0028. FIFO 5f loaded with 16-bit Galois LFSR states, initially ace1, next=(x>>1) XOR (b400 if x&1 else 0). Start 59=0828; poll 0c bit 0 (bounded ~1 s), clear 02/3f/59, FM Morse ID, RX and RF off. Wire order empirically low byte first, MSB first within each byte. No FIFO refill experiment yet. High rate above ~6500 cannot be represented by simple scaling of the 16-bit word; other modes not investigated.

Fine APC sweep: relative carrier power vs target 81: 81=0, 80=-0.39, 78=-2.06, 76=-13.02, 74=-14.89, 72=-17.21 dB; integrated SNR respectively 24.04,23.78,21.90,11.08,9.07,6.83 dB. Knee between 76 and 78. All six completed FSK and returned plausible ADC values; pre/post temperature/VREF ratio changed -0.0091% to +0.0474%, keyed battery ADC 17-18 counts higher than pre-TX, not evidence for physical voltage rise. MCU proxy cannot establish final temperature. No absolute watts. At target 76 the longer-frame trials recover more bits than at 78; receiver front-end overload (despite no ADC clipping) or a TX modulation/PA effect remain hypotheses, not established cause.

| APC | Nominal baud | Bytes | Assisted bit errors |
|---:|---:|---:|---:|
|72|1200|16|0/128|
|74|1200|16|0/128|
|76|1200|16|0/128|
|76|1200|128|0/1024|
|76|2400|128|3/1024|
|76|3600|64|11/512|
|76|4800|64|30/512|
|76|6000|16|40/128|
|78|1200|128|101/1024|

Full 18-capture results (including telemetry, received hex, correlation, symbol-clock estimate) in `docs/k6-rf-results/2026-11-19-fsk-matrix.json`. Raw IQ and per-trial inputs in `serial-test-output/tx-matrix/` (ignored, retained locally). Repo tools: `tools/k6_rf_matrix_capture.py APC BAUD BYTES` sends a single live RF request on the lab radio, refuses capture overwrite; `tools/k6_rf_matrix_decode.py serial-test-output/tx-matrix --output serial-test-output/tx-matrix/decoded.json` is offline. The latter searches the known payload for clock, start, filter length and polarity; therefore error counts are **assisted**, not blind BER or independent packet acquisition. Fixed SDR offset compensation +280375 Hz relative to the 433.750 MHz center, no transmitter calibration change. Weak correlation at 6000 can be a false match; do not claim support. Next: independent preamble/sync acquisition, repeat randomized payloads and CRC, vary SDR input attenuation, compare transmitter spectrum, enable verified independent TX timeout before sustained trials. Candidate Base API should accept bounded word spans and a characterized set of rates; no arbitrary register writes or raw percentage/watt claims.
