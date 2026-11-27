# FM vs GP0 microphone-source discrimination — K6 B-chip

Nov 27 2026. This is a follow-up to GP0 raw popcount measurements and the independent RFSim K6 microphone-source profile. **Stock radio / no hardware modifications** is a non-negotiable project requirement; direct MCU-ADC wiring is outside scope. The user authorized fast firmware and SPI experimentation without mandatory backup generation, but no software action substitutes for an attended physical RF transmission into a dummy load.

## Why we do NOT compare unkeyed FM hiss to silent Sotto

A no-carrier FM discriminator may output substantial noise because no RF carrier is quieting the limiter/discriminator. With a strong, unsaturated carrier, much of the receiver noise is suppressed. The source-side microphone input path can *still* contain noise independent of RF quieting; Sotto transmits encoded source noise just as faithfully as clean speech if packets arrive. Therefore, compare **matched strong-carrier FM transmissions**, using a no-carrier capture only to validate RF dynamic range and quieting, **not** as the transmitter's noise floor.

## Physical controls (receiver-only tooling + manually attended radio)

Receiver: physical RTL-SDR at **443.500MHz**, original sample rate **1,024,000**, center **443.375MHz** (125kHz nominal IF). Disable SDR AGC; **keep exactly 20.0dB tuner gain** in all recordings, as supplied by `tools/k6_sotto_sdr_capture.py`. Do not alter SDR coupling/antenna/attenuation between controls. Active transmitter attached to a dummy load, with receiver safely air-coupled or through proper attenuation. Do not connect a high-power RF cable directly to the RTL-SDR.

Four conditions, each a new ~5-second `.cf32` recording. The user physically controls PTT and confirms mode/settings; the host tools NEVER key the radio:

1. **No RF carrier**: K6 PTT released, candidate baseline saved already under `recovery/fm_source_qualification/no-carrier-20261127-01.cf32`, matching JSON. This is a 5s physical fixed-gain receive capture of 5,120,000 complex64 samples (40,960,000 bytes), SHA256 `b6be1223625048b098badf2b88de659853a1563c2bee6c825fad9561c56f6b8e`; receiver tuning/rate confirmed. **Radio-key state not independently verified**, so this remains a candidate baseline. It measured 3.49e-5 dimensionless IQ power, with no obvious strong carrier peak in its PSD near target (11.18dB maximum spectral bin over regional median, concentrated at an unrelated 128kHz spur); do not infer calibrated RF dBm or an absolute thermal noise floor.
2. **Analog FM carrier, external generator OFF**: keep analog FM TX enabled and physical PTT held, but leave generator output OFF. Sample *carrier-on microphone-source silence* at the same fixed 20dB SDR gain.
3. **Analog FM carrier, 1,000Hz / 0.1Vpp generator ON**: same antenna/coupling, analog FM mode, carrier power and radio audio settings. Physical PTT held during capture. Verify RFIC received generator signal (user's earlier plain FM check). This reveals the actual FM combined tone/residual SNR.
4. **RFIC internally generated tone with MICIN bypass**, only if an existing proven, safe module can provide it; path equivalence must be checked. This optional reference measures residual FM modulator + RF/SDR noise without the external mic source, but may traverse different emphasis/deviation controls. Don't assume it is comparable solely because it sounds pure.

The prior **GP0 RAW AIR** 500/1000/2800Hz raw popcounts and generator-OFF control are already CRC-verified (one partial 1kHz segment, do not fill holes). Their independently replayed original-decimator SNRs were 16.53/17.06/15.10dB for 0.1Vpp. The new FM comparison must be taken at the same nominal *external input* but analog FM/Sotto paths need not have identical deviation, filtering, levels or gain. An FM receiver with stronger RF than another capture can appear cleaner for reasons unrelated to MICIN.

## Safe receiver command for each new condition

After verifying the radio is ready to be manually keyed, start a fresh receive-only capture with a unique output prefix, for instance:

```sh
cd /storage5/dev/flux-gp0-popcount-air-20261127
mkdir -p recovery/fm_source_qualification
/home/tyler/.local/share/flux-rf-venv/bin/python -u tools/k6_sotto_sdr_capture.py \
  --seconds 5 --freq-hz 443500000 --gain 20 --device-index 0 \
  --out recovery/fm_source_qualification/mic-tone-1000-20261127-01.cf32 \
  --ready recovery/fm_source_qualification/mic-tone-1000-20261127-01.ready
```

The command prints `SDR_CAPTURE_READY` after actual verified USB samples begin; only then cue the operator to **press PTT**. Use a longer recording window (8s) if manual reaction time requires; the 5s example may be too short for slow operation. For a carrier-on segment, ensure the actual PTT interval lies inside the selected analysis window, and never silently treat an unkeyed segment as a valid control.

## Analysis tool and diagnostic caveats

`tools/k6_fm_source_qualify.py` reads only saved complex64 IQ; mixes from the **actual** carrier IF to baseband, filters channel to ~9.5kHz, decimates to 8kHz and bandpasses 250–3400Hz; it fits the 1kHz tone and reports residual deviation RMS, tone SNR, RF carrier power increase, and FM quieting. It does not control radio or SDR.

A receiver's **actual K6 FM carrier IF** must be measured from its strong silent-carrier RF spectrum before analysis. The observed GP0/FSK captures previously used a **−31.3kHz fine correction** versus the nominal 443.500MHz RF center; at 1.024MHz with SDR centered at 443.375MHz that would imply ~93.7kHz IF, but the actual analog FM carrier may differ and must not be assumed. The tool requires `--carrier-if-hz` explicitly. If analyzing already downconverted 128kHz complex64, the IF may be near zero.

```sh
$PY tools/k6_fm_source_qualify.py \
  --iq-rate 1024000 --carrier-if-hz MEASURED_CARRIER_IF_HZ \
  --start-s 1 --length-s 3 --tone-hz 1000 \
  --no-carrier recovery/fm_source_qualification/no-carrier-20261127-01.cf32 \
  --silent-carrier recovery/fm_source_qualification/silent-20261127-01.cf32 \
  --mic-tone recovery/fm_source_qualification/mic-tone-1000-20261127-01.cf32 \
  --out-json recovery/fm_source_qualification/matched-comparison-20261127-01.json
```

**Critical:** strongest 1kHz sine detection measures signal versus *everything except that fitted sine*, including FM distortion, source noise, RF/SDR hiss, oscillator jitter and channel AGC; this is not a standards-calibrated SINAD measurement. Flat radio RF output spectra and PTT timing must be independently verified. A no-carrier demodulated waveform is **not** the noise floor of a carrier-present FM receiver. If the internally generated tone and the microphone tone have different modulation deviation, pre/de-emphasis, or amplitude, do not subtract their residual noise powers as though comparable.

A large FM SNR advantage over GP0 with the same front-end stimulus would implicate the **GP0 digital extraction path**, not the analog MICIN itself, making no-mod hardware feasible. A similar poor analog FM residual is evidence to check jack, bias, RFIC gain, source level, and signal-generator cabling before pursuing another GP0 selector. Neither result alone isolates analog preamp noise from downstream codec/SDR distortion without independent controls.

## Offline verification

`python -m pytest -q tests/test_k6_fm_source_qualify.py` simulates strong FM carrier tone, no-carrier receiver noise and a noisier microphone source, plus input validation. Synthetic tests verify the **measurement code** only, not physical K6 fidelity. RFSim's source-audio model is at `tcarwash/RFSim`, experiment branch `experiment/k6-gp0-20261127`, commit `c708310`.

## November 27 physical FM check: negative RF-detection result (not audio SNR)

The operator confirmed the K6 red TX LED illuminated while the display showed **443.500 MHz** in analog FM. The audio generator was OFF and the dummy load attached. The first two receiver-only recordings were made with fixed **20.0dB SDR gain**, at 443.375MHz SDR center and 1.024MS/s: `silent-carrier-20261127-01.cf32` (9s, SHA256 `0e375fbee223669c8ad3aaf65a594f0dd9b2606b6ddef87749fbdb11f3f6c773`) and `silent-carrier-20261127-02.cf32` (17s, SHA256 `d77af33f302912fc3546c4c2b0da44e15a281904c217a03f486dbf775ca6b596`). Neither showed a meaningful RF power rise over a no-carrier baseline: <0.1dB throughout, with only existing receiver spurs near ±128kHz and ±256kHz.

A **33-step receive-only 430.0–455.6MHz sweep** (0.8MHz tuning steps, each with ~0.128s of IQ and manual 20dB gain) compared a new idle baseline against an operator-cued TX attempt. No swept center showed more than +0.04dB total IQ power rise. The sweep was sequential, used brief dwell per center, and is **NOT** an exhaustive continuous-time or fully gapless proof that RF wasn't emitted. Its center ±355kHz passband and center/edge exclusions can miss narrow frequencies or short transmissions. The original fixed-gain SDR was not proven equivalent to the auto-gain receiver used in successful GP0 RAW AIR RF captures.

A further receive-only idle gain sweep at **38 and 40dB** showed background powers ~1.01e-4 and ~1.25e-4 (normalized IQ power), peak IQ magnitude ~0.034 and ~0.039, with no clipping. A 16-second attended analog FM receive-only recording at fixed **40dB** (`silent-carrier-g40-20261127-01.cf32`, SHA256 `f4a18ae4648e21901ed1e62d036ae05c76bad04d4455c3eeb03bed3c5696acc4`) again found no RF power increase versus its same-gain idle baseline; each one-second bin was between approximately +0.08dB and -0.15dB of idle. This is still **not** a valid carrier-on silence recording; **do not demodulate it to claim an FM microphone noise floor**.

Likely missing control: use a **separate conventional FM receiver** on 443.500 simplex while the operator keys the K6 briefly into the dummy load. If its squelch opens, inspect K6 TX offset/split and RTL coupling/antenna/tuner; if it does not, inspect K6 ordinary FM RF power state, TX inhibits, and RF/PA initialization versus GP0 RAW AIR FSK without assuming any specific software defect. Distinguish a red TX LED from actual radiated RF. The R820T driver printed `PLL not locked!` on open in all capture runs, so tuner readback or independent known-signal reception should be verified before relying on the SDR as a fault witness. Do not retry long analog PTT dumps blindly; first secure independent RF presence evidence.

The source recordings and sidecars are under local `recovery/fm_source_qualification/`, intentionally excluded from git. The new offline sweep helper `tools/k6_rx_frequency_sweep.py` never accesses the K6 and cannot key/transmit. It was used for this evidence but is not yet a general-purpose RF spectrum analyzer or hardware-qualified detector.
