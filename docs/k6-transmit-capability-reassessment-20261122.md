# Flux K6 TX capability reassessment — after TX-15R physical audio-phase and Roger-beep work

**As of 2026-11-22 local / 2026-11-23 UTC.** Source branch: `research/native-fsk-pdm`, reviewed at `b352e55` (including `19e6bc7`, `a1427f0`, `c024490`). This is a **read-only evaluation of already archived experimental results**: no new RF, flashing, register writes, live SDR connection or serial activity. Other agents may be actively developing on the same worktree; this document must not be mistaken for an arming checklist or authorization to run a transmitter.

## Executive result

**Flux has a practical and repeatable serial-controlled RFIC-generated FM audio transmitter today.** The internal B-chip tone oscillator delivers reliable 150-update/s programmed tones, can repeat unchanged tone words without apparent audio-phase resets, and is increasingly compatible with continuous audio-oscillator phase across tone changes after accounting for the *real* source clock. It is **not yet** a device capable of independent programmable RF carrier phase and amplitude or arbitrary complex-IQ waveforms.

**Highest-payoff next investigation:** keep TX-15 frequency/clock physics and TX-15G gain physics separate. First fix/measure source timing with **distributed training pilots and a predeclared terminal guard**. Then determine whether `REG_70` forms a calibrated fast, monotone audio-FM *deviation* gain actuator and whether it can equalize the measured ~18.5% 502–2519-Hz audio rolloff. Only after separately understanding timing, phase, and gain should a combined programmable audio FM waveform be attempted.

**Biggest remaining distinction:** an arbitrary *audio FM modulator* is not an arbitrary RF **I/Q** generator. The K6's tone generator ultimately makes frequency deviation resembling `gain(t) × sin(audio_phase(t))`, subject to analog filtering and chip/SDR clocking. Its signed frequency-control waveform is constrained by oscillator phase/frequency/gain and update rates; REG_70 is not directly an RF amplitude actuator. APC can change RF amplitude independently, but has narrow usable range, low measured modulation bandwidth and phase coupling.

## Physical evidence hierarchy

| Route | Strongest verified physical result | What it really supports | Main barrier to arbitrary waveform |
|---|---|---|---|
| **TX-15 / REG_70/71 audio oscillator** | Two 192-slot real-RF runs, **143/144 and 144/144 heldout tone IDs**, 0 late writes; **0.383°/0.374°** same-tone recovered audio-phase RMS | Serial-controlled AFSK, multitone/FM tone streams, potential audio-domain waveform synthesis | Changed-frequency recovered audio-phase RMS **13.79°/30.35°** after pilot-only effective-delay fit; dynamic timing transfer and gain not yet controlled; RF carrier/IQ EVM unmeasured |
| **TX-10 binary 2-FSK native FIFO** | 4800-bit/s binary chips and recoverable 300-symbol/s eight-level PDM frequency alphabet; independent archived RF and SDR captures | Fast data transmission and binary-chip-based multilevel/experimental FM | Independent *ideal-FSK* complex IQ heldout EVM **107.08–171.08%** across four captures, sensitive to LO drift and model; not arbitrary coherent IQ |
| **TX-20 APC / REG_36** | Two real-RF threshold and high-side envelope repeats; **80–100%** controllable range; ~94% low-rate amplitude swing at **47.6 Hz**, ~39.5% at **91.1 Hz** | Slow AM/on-off envelope shaping near full output | Low-end hysteresis/fades after crossing ~70%, restricted dynamic range, control settling and RF phase pulling |
| **TX-12 MICIN internal ADC** | Guarded API26/tested firmware path, physical enable/filter switching and RF PA-off transient; **no physical analog injection to RF** | Potential analog FM-baseband source only with external fixture | Shared K-plug serial/mic use, unverified electrical adaptor and AC coupling; not a cable-free waveform source |
| **Roger beep / internal tone routing** | B-chip driver transmits internal tone in `REG_30=0xC3FA` with MICIN ADC **OFF**; actual TX-15 captures demonstrate RF FM tones | **Strong evidence that serial-port-preserving internal modulation is real** | No generic internal signed PCM or DAC-sample register demonstrated |
| **TX-11 native 4-FSK** | Receive-side DMR / 4-FSK code and working TX *binary* modem | A promising research lead | No B-chip native four-level TX mode proven |
| **TX-17 direct synthesizer offset** | Source/datasheet audit; RX-only preflight not a stable reference | Could in principle provide signed RF instantaneous-frequency writes | No confirmed fast signed-offset register; B-chip RX-only latch measurement not done |
| **TX-18 I2S or MCU PWM** | MCU PA11 PWM works to **speaker**, and alternate older-guide serial-audio blocks are described | Potential MCU/sample interface if a physical/digital RF TX ingress is found | No verified PCM-to-FM TX ingress or PA11-to-MICIN internal PCB route |
| **TX-21 FSK + APC simultaneous polar** | Offline composition of *separate* physical recordings only | A polar RF architecture hypothesis | Simultaneous hardware frequency+amplitude TX/EVM **not demonstrated**; joint physical experiment has an earlier safety-tool block and cannot be represented as tested |
| **TX-23 external I/Q board** | Architectural fallback only, no tested board on the K6 | Most direct conceptual route to genuine arbitrary RF I/Q | Hardware changes, RF mixing/LO and spurs, calibration and integration |

### TX-15P → Q → R: what actually changed

- TX-15P: **Two independent real RF captures** of 64 noncommensurate tone words gave **48/48** heldout tone IDs each; same-word phase jump RMS **0.424°/0.404°**. Initial ~110°/~101° changed-word apparent phase RMS used a fixed *nominal* 150/s clock and should **not** be interpreted as RFIC phase reset.
- TX-15Q: Train one source timebase from only three predeclared strong pilot frequency transitions and one effective audio phase delay from pilot. Pilot+delay corrected heldout changed-word recovered **audio phase** to **9.4°/12.7°**; heldout change midpoint error from **634/467 µs** to **17.9/20.1 µs** with the pilot clock correction. The rates were **149.558/149.683/s**, relative to the SDR sampling clock (not an independently measured CPU crystal frequency).
- TX-15R: 192 programmed updates, 48 pilot slots with 15 strong transitions, then 144 heldout. Firmware completed **192/192**, `late=0` twice; 143/144 and 144/144 heldout tone IDs. Pilot-fit rates **149.664/149.668/s**; heldout strong-transition median midpoint errors **24.5/58.8 µs**. Same-word audio phase RMS **0.383°/0.374°** over 95 heldout edges. Changed-word RMS after clock/one-delay fit **13.79°/30.35°** over 48 heldout edges. Inter-run changed-edge residual correlation **0.884**, but a post-hoc cross-run tone-pair correction helped one and hurt the other; cannot call it a validated stable phase calibration.
- **New TX-15R receive-observed tone amplitude response:** recovered FM discriminator sine deviation at 502 Hz ~**1,229 Hz**, at 2,519 Hz ~**1,001–1,002 Hz**, a **0.815 ratio** across both runs. This is ~18.5% *relative composite radio+RX response* falloff, not calibrated transmitter-only RF deviation or RF output power. This is an attractive, quantitative compensation target for a **future separately validated REG_70 gain experiment**.
- **Important terminal-slot warning:** run 01's lone failed ID is final slot 191; both final-slot sine fits badly degraded (~2,772/1,846 Hz RMSE). The second run's 144/144 count includes a correctly guessed but poorly qualified terminal symbol. Reserve an explicit **unscored tail guard**, fixed *before* the next recording, rather than silently excluding the last slot after viewing results.
- No TX-15 capture establishes global **433.500-MHz carrier phase**, calibrated 150/s clock, spectral compliance, coherent RF I/Q EVM, or arbitrary signed baseband.

### Why Roger beep is actionable but not arbitrary PCM

Source `firmware/k6-aura/build/aura-fd6818b-port/src/device/radio.rs` calls the B-driver's `tx_single_tone_on(..., true)` when transmitting Roger beep. The driver writes `REG_70` tone gain/enable, `REG_30=0xC3FA` TX tone state, and `REG_71` tone frequency. The voice state `0xC1FE` has `micin_en` bit 2 set, whereas transmitted tone state `0xC3FA` has **MICIN ADC disabled**. Thus TX-15 demonstrates the **internal tone FM modulation path** with serial connected. It is **not** digital samples routed into an internal MICIN DAC, and `REG_47`'s AFOUT speaker selection isn't a TX PCM port.

The alternate MDC1200 Roger setting uses the FSK FIFO. MCU PA11 PWM drives the speaker amplifier; local board notes do not establish a return to MICIN. No new hardware path has been proved by the Roger beep itself.

The [TX-15G gain handoff](k6-tx15-roger-beep-reg70-gain-handoff-20261122.md) correctly proposes fixed-frequency gain sweeps, including same-gain writes and zero-gain leakage, *after* the existing frequency-phase experiment. Specifically distinguish:
1. `REG_70` **tone-source amplitude** -> **amplitude of instantaneous FM deviation** (possible), versus
2. `REG_36` APC **carrier RF envelope** (measured but slow), versus
3. RF carrier **phase** and arbitrary complex RF I/Q (not yet controlled).

If REG_70 is monotone, fast enough and does not reset the tone, later gain-vs-audio-frequency lookup data can test pre-emphasis/equalization of the observed 18.5% falloff. This is *not* an arbitrary-waveform DAC until proven to generate signed independent baseband samples, which changing the gain of an audio oscillator normally cannot.

### Why APC does not yet rescue arbitrary IQ

- Real RF APC threshold sweeps show repeatable fades near 70% and recovery only toward 80%; use a provisional **80–100%** safe experimental range, rather than extrapolating to 0–100%.
- Two high-side modulation repeats retain ~**94%** relative amplitude swing at **47.6 Hz**, but only ~**39.5%** at **91.05 Hz**, relative to ~24.3 Hz. This is a compound MCU/SPI/APC/receiver measurement, not an intrinsic PA-only closed-loop bandwidth.
- Separate APC-only RF captures show directionally consistent ~**+0.14/+0.22 rad** 100→80% and **−0.28/−0.26 rad** 80→100% local phase-fit offsets; may reflect carrier pulling, transient settling or receiver response, rather than instantaneous phase jumps.
- Counterfactual FSK phase × independent APC amplitude replays produced ~**4.5% amplitude-only** optimistically aligned error, **not joint-hardware RF-IQ EVM**. The target reused the measured FSK phase and the two controls were never actuated together. Don't use 4.5% to justify QAM/PSK or full arbitrary IQ claims.

### Native FSK's narrower but significant achievement

Physical binary-FSK/PDM decodes are meaningful for **new digital modes**. The 7-tap cross-recording pilot model reached heldout *chip-boundary phase coherence* of ~0.77, 0.70, 0.83, 0.24; this is a strong source/SDR response clue but **not 704-bit all-sample IQ EVM**. The strict frozen-pilot EVM was **120.94%, 107.08%, 110.50%, 171.08%** vs *an ideal instantaneous 2-FSK reference*, which is a very severe and receiver-conditioned benchmark. These results can coexist: data bits may be robust while absolute phase trajectory diverges. The trained captures also all use the *same payload*, so performance for a genuinely **new** payload remains untested.

Native 4-FSK TX cannot be inferred from the FD6818B's DMR **receive** demodulator. The direct synthesizer signed-offset path is equally unverified; old non-B register-latch results cannot be assigned to the B chip.

## Ranked next steps, with explicit evidence gates

1. **TX-15S — distributed timing pilots & tail guard: highest impact on existing path.** With total duration within current module lease, place four *predeclared* ~12-slot reference regions spread through a 192-slot burst; hold out the other slots and declare a terminal unscored tail guard before capture. Learn clock timebase **only** from the pilot regions (piecewise-linear / low-order regularized model), compare against pilot-only prefix linear clock on same heldout transitions. Use at least two independent RF repeats and document final RTL-vs-MCU time drift. A real improvement needs to lower heldout edge-time errors and changing-tone phase RMS *without heldout refitting*; don't specify optimistic absolute error limits as if already attained.
2. **TX-15G — fixed-frequency REG_70 source-gain step response: next new actuator.** Keep `REG_71` fixed around 1.2–1.5 kHz, preserve other TX settings. Use bounded predeclared gain steps `g = 0,8,16,32,48,60` only after verifying B-chip gain word semantics, phase continuity, time/rate and watchdog in an inert module emulator. Two independent attended dummy-load captures if safe. Derive monotonicity, RF discriminator deviation vs gain, zero-gain residue, filter transients, lag and same-gain write artifacts. Do **not** combine REG_70 sweep with TX-15S's training run: the two variables would become confounded.
3. **TX-15 audio-frequency equalizer & actual target waveform.** If TX-15G succeeds, use pilot-only measured gain-frequency response to preequalize the 502–2519-Hz composite rolloff with a **predeclared** real waveform (tone sequence/chirp/low-rate shaped FM) and heldout verification. Report both **recovered audio-waveform fidelity** and independently defined **RF complex-IQ EVM**, not just tone-ID accuracy. Distinguish clock transfer and radio+SDR response from source-only measurements.
4. **FSK-27 new-payload / clock-transfer validation.** Train the measured response on an independently chosen binary/PDM packet and test against a *different* payload with frozen timing/CFO/model. Use 4800-baud FIFO real RF records and a calibrated receiver where practical. This establishes digital-mode generalization separately from ideal RF phase accuracy. No joint APC test while the prior physical safety-tool blocker is unresolved.
5. **TX-17 B-chip RX-only PLL-latch audit when a stable carrier exists.** The current RX preflight at 162.550/162.475 MHz was insufficient; avoid register writes without a reliable independently observed reference. Examine chip-specific `0x38/39` readback/latch safely and revert; still not necessarily fast *phase-coherent* modulation.
6. **Secondary long-lead paths:** native 4-FSK TX evidence, B-specific digital PCM ingress/I2S, CTCSS subaudio signed-offset experiments, and finally a separate DAC/mixer/IQ daughterboard if **true arbitrary complex IQ** is the non-negotiable goal. Protect all avenues in the master TX queue rather than interpreting a good TX-15 result as proof that they are unnecessary.

## Strategic answer

- **For usable experimental transmit modes:** pursue TX-15S + TX-15G now; native binary FSK already gives a separate fast modem foundation.
- **For arbitrary controlled FM (frequency trajectory):** TX-15 can generate *restricted* internally synthesized audio tones and perhaps gain-shaped audio-FM, but timing, sign/DC freedom, and waveform error must be demonstrated. Native FSK/PDM can generate fast *quantized frequency states* but currently lacks coherent waveform accuracy.
- **For arbitrary RF IQ/QAM/PSK/OFDM:** insufficient evidence to call the on-board FD6818B a general-purpose arbitrary waveform engine. APC is an especially severe bandwidth/dynamic-range bottleneck. Long-lead direct-synthesizer digital input or external IQ modulator is still needed if arbitrary coherent I/Q is the actual definition of done.

## Evidence references

- `docs/k6-tx15r-long-pilot-physical-20261122.md` (`b352e55`)
- `docs/k6-tx15q-clock-transfer-physical-20261122.md` (`19e6bc7`)
- `docs/k6-tx15p-physical-phase-evidence-20261122.md` (`a1427f0`)
- `docs/k6-tx15-roger-beep-reg70-gain-handoff-20261122.md` (`c024490`)
- `docs/k6-fsk-physical-iq-holdout-20261122.md`
- `docs/k6-fsk-phase-transfer-20261122.md`
- `docs/k6-apc-fine-hysteresis-bandwidth.md`
- `docs/k6-polar-combined-iq-offline-20261122.md`
- `docs/k6-tx11-multilevel-source-audit-20261122.md`
- `docs/k6-tx17-direct-fm-register-audit-20261122.md`
- Exact archived SDR evidence under `/home/tyler/flux-bench/tx-injection-20261122/`, including `tone-oscillator/tx15r-long150-crossrun-v1.json`

Do not confuse a *successful audio-frequency oscillator waveform* with controlled RF-carrier complex-IQ, and do not confuse the **separate physical RF experiments** of FSK and APC with a joint polar transmitter.
