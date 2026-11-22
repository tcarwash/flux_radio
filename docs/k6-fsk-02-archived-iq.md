# Flux FSK-02: archived native-FSK I/Q phase and startup analysis

2026-11-22. Worktree: research/native-fsk-pdm. **Offline-only**.
No live radio, serial interface, receiver acquisition, flash, PTT, or RF TX.
This is a supporting finding for the [all-path TX experiments roadmap](k6-tx-next-experiments.md).

## Inputs and reproduction

Archived hardware RF captures remain **outside Git** at:

    /storage5/dev/flux-serial-throughput/serial-test-output/tx-matrix/

Format: interleaved uint8 I/Q at nominal 1,024,000 complex samples/sec,
mix center by +280,375 Hz and decimate to 128,000 samples/sec. This uses
the same prior frequency-offset assumption as the original analysis.
The existing decoded.json supplies the **known-payload-assisted** start
time, recovered bit clock, and original errors. It is not a blind sync.

Run:

~~~sh
cd /storage5/dev/flux-native-fsk-pdm
python3 -m pytest -q tests/test_k6_fsk_iq_edges.py \
    tests/test_k6_native_fsk_pdm.py \
    tests/test_k6_fsk_waveform_bench.py
python3 tools/k6_fsk_iq_edges.py \
    --data-dir /storage5/dev/flux-serial-throughput/serial-test-output/tx-matrix \
    --out /tmp/flux-fsk-02-archived-20261122.json
~~~

The analyzer produces capture hashes, byte/symbol rates, median discriminator
state frequencies, startup vs settled amplitude contrast, error positions,
settled midpoint drift, transition rise-time estimates, and phase-bridge
residual diagnostics. Original IQ is neither changed nor copied into Git.
All results can be regenerated from the preserved 2026-11-19 recordings.

## Results from the saved FD6818B 0x4829 captures

The exact files are apc76-baud{1200,2400}-len128.npy and
apc76-baud{3600,4800}-len64.npy, at the same experimental APC target 76.

| Bit clock | Archived assisted bit errors | Second discriminator errors | Second discriminator errors after bit 168 | Approx state separation |
|---:|---:|---:|---:|---:|
| 1200/s | 0/1024 | 1/1024 | 1 | 11.37 kHz |
| 2400/s | 3/1024 | 2/1024 | 0 | 11.17 kHz |
| 3600/s | 11/512 | 9/512 | 0 | 11.04 kHz |
| 4800/s | 30/512 | 28/512 | 1 | 10.99 kHz |

The original 2400 errors were bits 0,1,3. At 4800, its 30 errors were
all in the first 168 bits. The second decoder found 27/28 errors inside
the first 168 bits and one later. **This is consistent with startup or
assisted-acquisition effects, not proof of a particular hardware cause.**

### New receiver DSP comparison: filter complex I/Q first

Keeping **exactly the same known-payload-assisted clock and center times**,
a third diagnostic passes the complex RF baseband through a third-order
8-kHz Butterworth low-pass (zero-phase forward/backward offline filtering)
**before** taking the frequency discriminator. The original unfiltered
edge/phase measurements are retained separately.

| Bit clock | Unfiltered simple discriminator | 8-kHz I/Q low-pass then discriminator | Errors after bit 168 |
|---:|---:|---:|---:|
| 1200 | 1/1024 | **0/1024** | 0 |
| 2400 | 2/1024 | **0/1024** | 0 |
| 3600 | 9/512 | **2/512** | 0 |
| 4800 | 28/512 | **6/512** | 0 |

At 4800, all 6 remaining errors were in the first 82 bits (last error
bit 81). On that same capture, further cutoff sweeps yielded 8 errors
at 10 kHz, 5 at 6.5 kHz, and 6 at 5.5 kHz. This confirms that **receiver
filter placement and bandwidth strongly affect the high-rate error
count**. It does *not* certify a 4800-bit/s TX or prove a blind receiver:
the reference timing and bits remain known, and offline zero-phase
filtering is noncausal. A live receiver needs a causal equivalent with
known group delay and independent synchronization.

This is actionable before any hardware work: upgrade the reference
decoder and assess startup acquisition separately from true RF
state/phase settling. The more robust I/Q-filtered frequency level
separation at 4800 was about 10.22 kHz, compared with 10.99 kHz before
filtering. This reinforces that single-number separation depends on the
receiver estimator and filter.

The apparent RF state separation is a *relative SDR-discriminator estimate*,
not a calibrated RF transmitter deviation. Under this alternate filtering
and decision method it is about 11 kHz, whereas an earlier limited analysis
reported approximately 7-8 kHz; the discrepancy is unresolved. It must be
resolved by a fixed-gain, instrumented, repeatable conducted test before
selecting any actual native-FSK deviation register value.

Median received amplitude relative to an earlier pre-burst noise estimate:
4800 capture 1.26x in first 32 bit centers vs 2.56x in the settled region;
2400 1.97x vs 2.63x; 1200 2.68x vs 2.60x. The weak high-rate first-bit
contrast supports repeating a longer known preamble and an independent
receiver synchronization experiment.

### Edges and phase diagnostics (not a continuity proof)

The tool selects settled isolated 000->111 or 111->000 transitions, takes
the median of normalized discriminator edges, then uses two fitted slopes
inside the preceding and following bit to estimate a *phase bridge*.
This includes any smoothing, group delay, receiver noise and center-time
alignment error, so a nonzero bridge is **not necessarily a phase reset**.

| Baud | Isolated transitions after quality guard | Approx median 10%-90% edge duration | Median abs phase bridge |
|---:|---:|---:|---:|
| 1200 | 38 | 508 us | 0.90 rad |
| 2400 | 38 | 250 us | 0.48 rad |
| 3600 | 10 | 250 us | no same-state control |
| 4800 | 9 | 148 us | 1.52 rad |

The corresponding same-state bridge medians were ~0.24 rad at 1200,
~0.25 rad at 2400, and ~0.14 rad at 4800 (only **one** qualifying 4800
control, not statistically meaningful). The edge durations vary with bit
period and include digital smoothing; they should not be treated as direct
RFIC step-response times. The 4800 edge/bridge sample is small and noisy.

**Consequence:** archived IQ does not yet establish continuous or resetting
transmitter phase. A deliberately designed sequence with frequent **long
runs and isolated edges**, fixed receiver gain and a long sacrificial
preamble is required. An independent waveform generator or simulator should
supply a known reset-versus-continuous control (synthetic test fixtures
exercise this distinction without radio operation).

## FSK-03 register triage: what to characterize next

| Register | Existing evidence | What it does NOT yet prove |
|---|---|---|
| 0x40 | B driver writes 0x3516 for ordinary modulation; BK4819 family documentation calls it the RF FM-deviation control | Exact 0x4829 native-FSK deviation-vs-field calibration, during the raw modem mode |
| 0x70 | B driver uses 0x00E0 for ordinary FSK and 0x0080 \+ gain (16..96) in the isolated serial FSK laboratory | Whether changing its lower FSK-gain field changes *frequency separation*, output amplitude, or both |
| 0x72 | Driver sets 0x3065 for 1200-bit/s FSK; serial laboratory sets scaled words for 2400/3600/4800 | Accurate RF symbol-clock response or glitch-free changes while active |
| 0x59 | TX FIFO clear/enable and frame format control; serial lab uses 0x00f8 base and 0x08f8 enable | Transparent continuous symbols, preambleless operation or gap-free frame chaining |
| 0x58/0x5C/0x5D/0x5F | FSK framing, sync, length and FIFO | Raw continuous-stream modulation with phase continuity across frame boundaries |

The archived matrix swept **APC target and baud**, not a calibrated 0x40
deviation-register matrix. The serial-lab code has a gain parameter and
the captured matrix's primary variable named target is PA target; do not
mistake that for calibrated FSK deviation.

Related source: the generated pinned B driver in
firmware/k6-aura/build/aura-fd6818b-port/src/drivers/fd6818b.rs;
firmware/k6-aura/apply-serial-lab.py;
firmware/k6-aura/apply-configurable-fsk.py.

External **family-level, not B-chip-proven** corroboration:
- https://github.com/egzumer/uv-k5-firmware-custom/blob/main/driver/bk4819.c
- https://github.com/chouyu/uv-k5-firmware-ssbtx/blob/main/driver/bk4819.c
- https://www.glaver.org/blog/UV-17/FD6818.pdf

## Next actions (no unapproved RF)

1. Make a repeatable hardware-free register-map audit listing the **exact
   source lines** for REG_40/70/72/59 and how the lab controls reach them.
2. Compare the known 1200/2400/3600/4800 archived IQ with alternate
   discriminator bandwidth/phase windows to resolve the ~7-8 vs ~11 kHz
   discrepancy and quantify receiver-processing sensitivity.
3. Design an **inert-by-default** arbitrary-word FSK laboratory module
   with emulator tests and a protocol that explicitly describes preamble
   symbols, bit ordering, bit clock, 0/1 runs and guard time.
4. Only after verified recoverable firmware, independent TX timeout, rated
   load/coupler/attenuation, operator present and explicit consent:
   capture short conducted trials with several supported 0x40/0x70
   settings, measuring both transmitted frequency levels and phase.
   Start with stock settings and the same fixed-gain SDR configuration.
5. Compare actual captured IQ with the [frequency-PDM waveform bench]
   (k6-native-fsk-pdm.md). Accept only demonstrated phase fidelity,
   occupied bandwidth and acceptable adjacent-channel emissions.

**No result above establishes real 4/8-FSK, MSK, or general-purpose RF
waveform synthesis on the FD6818B.** The preserved alternative TX paths
(MICIN, TCXO, digital I2S, native multi-level and external modem) remain
in the all-path roadmap.
