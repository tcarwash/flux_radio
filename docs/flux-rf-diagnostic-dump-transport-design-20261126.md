# Flux: reusable RF diagnostic data dump when live serial is unavailable

**Design note / field-proven primitive, not a generic module yet.** Captured Nov 26, 2026 PT with the Quansheng K6 B-chip running Flux API30. Related experimental implementation: `c/k6/mod/sotto_gp0_pcm_air_tx.c`, `c/k6/mod/sotto_gp0_pcm_air.h`, `tools/k6_gp0_pcm_air_rx.py`, and `tools/k6_gp0_pcm_air.py`. See [GP0 physical results](sotto-gp0-pre-vocoder-physical-results-20261126.md).

## Why keep this capability

The programming cable and external microphone share the K6 accessory connector. Live serial may be absent, incompatible with active microphone acquisition, or unavailable to a given module. Yet the radio can produce a bounded piece of data **in RAM**, stop the producer, then transmit it over the **existing authorized RF hardware packet path**. A standalone RTL-SDR or another receiving radio can recover and validate it. No active control link back to the transmitter is necessary for a one-way diagnostic.

The actual `GP0 PCM AIR` capture demonstrated two **67/67 fully decoded, CRC-valid RF sessions** reconstructing byte-exact 1600-sample PCM payloads, plus safe partial recovery of one 36/67 and one 20/67 session. Thus the transport is not purely simulated. Audio correctness and sample-clock rate must still be analyzed separately from the transport.

## Proven data-plane mechanics

- **Radio producer:** bounded module scratch, zero dynamic allocation, capture from an existing device service and store in RAM; stop hardware/DMA before FSK begins. Never put SPI or RF transfer inside the audio ISR. Strictly enforce module arena limits.
- **Transmitter:** existing hardware-cooperative FSK (`fsk_async`), 4,800 bit/s L64 framing with 16 × `0x55` preamble and public sync. Current specific PCM diagnostic uses 67 transmissions of **66-byte RFIC FIFO words** (64 byte whitened data, plus 2-byte guard), transmitting roughly 11–13 seconds. This is not a streaming audio replacement or Sotto codec packet.
- **Payload:** current example is D9/version1, seq (0..66), total (67), session token, PCM length/rate, capture-duration ticks, 24 exact `int16` samples per data packet, CRC16/CCITT-FALSE, PN9 whitening; guard word protects the RFIC FIFO shift. The 67th packet has well-defined zero padding.
- **PC receiver:** independently demodulates blind preamble/sync from 128 kS/s complex64 RTL-SDR IQ, accepts frames only on CRC and identity consistency, deduplicates/reorders, and checks completeness. It *never guesses* dropped bytes. Capture IQ with a separate read-only process, not with the simultaneously running Sotto Monitor on the same RTL-SDR.
- **Operator controls:** dummy load for bench experiments, licensed/authorized frequency, physical PTT held throughout, bounded watchdog / TX lease, no unattended keying, module checks user aborts/timeout every packet, and always returns to RX. In the specific PCM module the frequency gate is **443.500 MHz**, B-chip, API30.
- **Privacy:** amateur-radio RF traffic is public and may be recorded by third parties. **Never dump private keys, factory calibration, personal contents, proprietary secrets, or full raw SPI backups over RF.** Only use deliberately chosen non-sensitive diagnostic telemetry and identify the station as applicable. Keep raw IQ, WAV, module keys, and SPI backups untracked/private.

## Generalizing this to a small `RF Dump` library later

Keep the existing empirically validated packet acquisition and output decoder, but decouple *payload kind* from *RF framing*:

1. **Capture adapter** produces a finite immutable byte buffer plus metadata (`kind`, length, optional sample rate/capture ticks, schema version). Start with RAM sensor counters, fault trace, timing histograms, audio sample snippets, and a small structured experiment result. Do not require a full serial stack.
2. **Packetizer** sends `session_id`, `transfer_id`, payload version/type, `seq`, `total`, payload length, per-packet CRC, and optional *whole-transfer checksum*. The current D9 PCM format already illustrates 64-byte data framing but must not be misrepresented as a generic format. If designing `RF Dump v1`, use its **own new magic and version** rather than collision with D9/Sotto A9/FDV1.
3. **Receiver** writes a *versioned run manifest* with accepted/rejected packets, missing indices, integrity status, RF tune hypothesis, transfer metadata, and a raw data file only on complete verification. Preserve incomplete IQ and verified partial chunks separately (with explicit offsets and missing ranges) for offline salvage. Never manufacture missing samples or accept a partial transfer as complete.
4. **Reliability enhancements** after first feasibility tests: optional sender-side bounded repetition of lost-prone chunks or light parity/FEC and a predictable preamble; a low-overhead ending summary frame; optional manual repeat/resume under new session with validated matching transfer IDs. No ARQ if there is no RX control path. Keep airtime, RAM, CPU, and TX safety bounded. Prefer removable modules to flashing the MCU.
5. **Receiver pluggability:** capture from an RTL-SDR now; later accept receiver IQ recordings, Soapy/other SDRs, or physical FIFO packets as adapters. Let `rfsim` validate bit errors and packet loss, but never replace actual SDR CRC evidence.
6. **Practical verification:** first synthetic golden vectors + independent C/host parser, then dummy-load RF capture, replay-loss simulation, duplicate/conflicting packet rejection, zero-PTT/cancel return to RX, and **full-device post-use status**, before shipping it as a core transport facility. Validate station identification/operating rules for the selected frequency and mode.

## Engineering lessons from real data

The two complete `-01` tone captures establish that **67/67 CRC-validated chunks can cross the live air interface**; the new `gen-500-02` captured only packets `0..19` while still reconstructing an authentic **480-sample prefix**. Both results matter: retain and analyze partial diagnostic data instead of throwing away the entire capture, but **never substitute a prefix for the declared full buffer** or silently fill its tail. The same packet header's MCU capture-time ticks independently diagnosed a ~6.6% fast PCM producer; useful metadata matters as much as the bytes.

The strongest next application is an **exception/fault telemetry dump**: a very small module takes an already captured boot-local diagnostics record, emits a bounded number of tagged packets, and the PC reconstructs it without consuming the headset/programming jack. This is a design direction only; the existing K6 GP0 PCM AIR module is the only on-radio RF diagnostic transport installed here. An RF dump can itself perturb an experiment by occupying the RF path; when possible acquire complete diagnostics *before* transmitting and transmit only after stopping the measured subsystem.
