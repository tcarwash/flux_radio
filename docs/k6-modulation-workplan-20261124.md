# Flux K6-B modulation expansion: parallel implementation plan

**2026-11-24 · proposal + active offline research; NOT a hardware TX capability announcement.**

This adds an actionable work plan to the broader `docs/k6-rf-expansion-plan.md`
(November 18). It incorporates a read-only Antigravity design review
(`agy-jobs` job `60dd8d1bc865`, explicit `gemini-4-hydrogen-medium`) and an
independent audit of current main and selected old CW / IF / MICIN worktrees.
The Antigravity proposal was **not** a hardware test and did not edit code.
User-requested modes: **AM, clean-sounding CW, SSB, OFDM, QPSK**.

## Principle and evidence

The FD6818B K6 has proven FM/FSK TX primitives, a tone-on-FM path, receive IF
sampling, and an AM RX mode. It does **not** currently expose a verified,
linear, arbitrary complex I/Q RF transmitter or a fast/monotonic independent
RF-envelope control path. Do not confuse AM/SSB RX, audio carried over FM, or
host-generated IQ with radiated true AM/SSB/QPSK/OFDM. Existing native-FSK,
SoapyFang, and Sotto Unicorn tests establish useful software behavior, not
physical silicon timing, emissions, or PA linearity.

Existing evidence to reuse, not rewrite:

- `docs/k6-rf-expansion-plan.md`: general RF architecture, constant-envelope
  distinction, CW keying/ramp and safe measurement gates.
- `docs/k6-if-modem.md` and `sim/k6if/psk.py`: PSK/SSB-IF receiver modelling,
  differential QPSK, and weak-signal comparisons; these are **not** QPSK TX.
- `docs/k6-afsweep.md`, `docs/k6-surf.md`: AM/DSB/USB receive behavior and AF
  register selection; avoid interpreting speaker demodulation as RF modulation.
- `research/modulation-micin-70cm`, `research/tx-injection`,
  `experiment/cw-manual-envelope`, `fix/cw-keyup-rf-cut` and related CW/PA
  worktrees: preserve failed and partial real-RF experiments and adverse APC
  outcomes when designing tests. Do not adopt their register recipes on
  another board or bypass measured restrictions.
- `tools/k6_sotto_mock_qualify.py`, `sim/k6emu`, SoapyFang and KISS fake HIL:
  no-hardware execution and regression harnesses. An offline waveform is not
  automatically an installed modem or KISS provider.

## Priority and roadmap

| Mode | Priority | Offline deliverable now | Radio integration and hardware gate |
| --- | --- | --- | --- |
| CW (A1A) | **P0, nearest useful TX improvement** | WPM-timed keying, continuous-carrier envelope, 4–8 ms raised-cosine shaping; compare ideal hard edges vs smoothed spectral leakage; distinguish CW RF from sidetone/keyer UX | Guarded 70cm conducted SDR capture of real APC step/knee/lag, RF click spectrum and off-state leakage; select a ramp only if actual envelope monotonic and key-up terminates promptly; fallback is existing PA/RX gating, not silent RF leakage |
| QPSK | P1, digital baseband path | Gray QPSK/DQPSK, RRC shaping, matched filtering, deterministic BER/EVM/CFO tests, compare ideal linear IQ with phase-only approximations | Existing `sim/k6if/psk.py` studies IF reception; TX requires a proven phase trajectory for constant-envelope variant, and a linear envelope path for conventional RRC QPSK. No production/KISS mode until physical path and framing validated |
| AM | P1, learn amplitude constraints | True RF AM vs suppressed-carrier DSB, overmodulation, carrier/sideband, THD and distortion under *hypothetical* APC response | AM RX already exists. True AM TX needs calibrated fast, linear envelope. MICIN AF into FM isn't RF AM; APC monotonicity, timing and safe PA behavior are the gate |
| SSB (USB/LSB) | P2, RX-first | Hilbert/analytic IQ, opposing-sideband rejection, PAPR and phase/envelope mismatch in polar model; consider GP0 IF RX and existing host PSK study | Software-generated USB is not real K6 RF USB TX. Needs a linear modulation path or external hardware; RX can advance first; voice mode, not KISS |
| OFDM | P3, research/host-only | Small-FFT OFDM, CP/ISI, QPSK carriers, CFO/ICI, PAPR, clipping and EVM/BER/PSD | Stock saturated FM PA and missing arbitrary IQ are blockers; do NOT add as stock firmware TX. Revisit only with an independently measured external linear/IQ RF chain |

Priority here is based on **feasibility and engineering leverage**, not the
order of labels in the original suggestion. All five initial implementations
are safe host-only prototypes. QPSK is a more modest stepping stone than OFDM.

## Phase gates

1. **Offline waveform reference:** signal generation and receive/demod tests
   with deterministic seeds and ground truth; PSD, occupied bandwidth,
   envelope, PAPR, EVM, and BER where applicable. Exercise failures, parameter
   bounds and noise, not merely attractive plots. Keep separate from `Base`.
2. **Offline mock integration:** optional virtual stimulus, register-event
   traces, SoapyFang/Unicorn receiver injection, and deterministic regression.
   Do not claim the current K6 has an arbitrary complex TX just because a host
   IQ generator exists; do not key or flash physical hardware in these tests.
3. **Hardware capabilities decision:** use the existing GitHub experiment/bench
   queue and hardware lock when the radio becomes available. Board/RFIC ID,
   power mode, dummy load, coupler+attenuation, SDR gain, timing and raw IQ
   captures must be recorded. Verify APC envelope monotonicity and key-up
   isolation for CW/AM, phase/frequency response for constant-envelope QPSK,
   and a truly linear path before SSB/OFDM TX can be considered. Conservative
   PA limits, legal band/mode restrictions and explicit operator control are
   preserved. Non-working experiments are valuable evidence and should be
   documented rather than hidden.
4. **Only after physical proof:** assess firmware APIs, memory budgets, RX/TX
   packet framing, pack placement, safe watchdog/recovery and KISS registration.
   CW and voice AM/SSB are not KISS packet providers. Data modes can become
   providers only when actually able to send/receive framed packets. No new
   Base firmware features are authorized by this plan.

### Offline acceptance targets (engineering hypotheses, not RF guarantees)

- CW: finite, bounded 4–8 ms shaping with zero post-key-up level; stable WPM
  timing; spectral leakage lower than hard keying on matched-duration signals.
  A good simulated spectrum is **not** proof that APC produces that shape.
- AM: modulation index and overmodulation checks; single-tone carrier and
  both sidebands in a calibrated FFT; no false claim about PA output.
- SSB: exact USB/LSB analytic-signal conventions and substantial opposite
  sideband rejection on a coherent tone; note RX image/DC leakage limitations.
- QPSK: noiseless exact bit recovery with matched assumptions; deterministic
  nonzero-noise BER/EVM trends and phase-offset tests. No implicit real RF TX.
- OFDM: exact no-impairment packet recovery, bounded/identified carrier sets,
  CP validation, PAPR observation and demonstrably worse EVM/BER with clipping
  or offset. Expected PAPR is a warning for a saturated PA, not an invitation
  to use one.

## Parallel Muse workstream assignments (started)

All five branches begin at `origin/main` commit `1d376919` and use isolated
worktrees. Worker changes are **not** authorized to edit radio-control code,
Base/Data packs, the KISS registry, old experiment branches, or the shared
`sim/k6if/psk.py`. No module installs, hardware, live serial, RF transmission,
GitHub pushes, or commits from workers. Each worker owns exactly two new files:

| Workstream | Muse job | Writable files | Acceptance test |
| --- | --- | --- | --- |
| CW envelope | `fb522745042d` | `sim/modulation/cw_envelope.py`; `tests/test_modlab_cw_envelope.py` | Correct key timing, bounded ramp, deterministic lower spectral leakage |
| AM model | `8152cfef700c` | `sim/modulation/am_waveform.py`; `tests/test_modlab_am_waveform.py` | Carrier, symmetric sidebands, envelope depth, overmodulation rejection |
| QPSK lab | `42f1106c2940` | `sim/modulation/qpsk_lab.py`; `tests/test_modlab_qpsk_lab.py` | Gray/RRC, zero-noise bit-exact roundtrip, noise/EVM baseline |
| SSB IQ lab | `747047330186` | `sim/modulation/ssb_iq.py`; `tests/test_modlab_ssb_iq.py` | USB/LSB sign and suppression, DC/scale/crest bounds |
| OFDM lab | `b9e16198cf3e` | `sim/modulation/ofdm_lab.py`; `tests/test_modlab_ofdm_lab.py` | Small-FFT CP roundtrip, PAPR, CFO and clipping penalty |

The Muse queue has a 3-worker concurrency cap, so SSB and OFDM start queued;
the first three are scheduled concurrently. This decomposition refines
Antigravity's W1–W5 architecture proposal into five **file-independent** mode
prototypes before imposing a shared `WaveformSpec` contract. The coordinator
must review each output, run pytest and independent numerical checks, and
then decide whether to consolidate metrics under a shared simulator-only
interface. A later independent phase can add `sim/k6emu/tx_trace.py` and a
simulator UI lab panel as Antigravity suggested; these are not required to
make the initial algorithms correct.

## Current state, limits and handoff

- This plan itself makes no production modulation claim or hardware change.
- Antigravity job `60dd8d1bc865` completed read-only with a mode-specific
  evidence review; local report is accessible through `agy-jobs result`.
- The Muse jobs are isolated investigations/implementation; completion of a
  worker is **not** acceptance. No automatic merge to main. Preserve negative
  test results and research evidence.
- Stage 2 emulator bridge and exact STM32/FD6818B/Aura ABI constraints must
  be resolved after the individual waveforms pass. Existing Sotto/KISS
  integration is independent and must remain untouched.
