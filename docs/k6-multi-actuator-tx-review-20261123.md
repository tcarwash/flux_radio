# Flux K6 multi-actuator transmit research — morning reassessment

**As of 2026-11-23 06:13 PST.** Review of two *separate* active Git worktrees and archived physical RF evidence. This is a read-only assessment: **no new transmit, register writes, flashing, hardware changes, or test captures**. Not all of these findings are present in the same branch.

- **Tone / phase / gain / synthesizer branch:** `/storage5/dev/flux-native-fsk-pdm`, `research/native-fsk-pdm`, reviewed at commit `05c71e9`. The physical TX15W report was present as an **untracked file** `docs/k6-tx15w-gain-physical-20261122.md`, with two independently archived physical RF records. Other untracked work was in progress and was left untouched.
- **Subaudio / DCS branch:** `/storage5/dev/flux-tx16-subaudio-modulation`, `research/tx16-subaudio-modulation`, reviewed at commit `cc99cd3`. TX16K untracked source/tests/receiver experiments were in progress and left untouched. **These commits have not been merged into the TX15 branch**; do not run `TX16*` source from the other branch without examining its intended source/ABI and fixed safeguards.

## Decision in one paragraph

**A real dual-source FM transmitter is proven, not merely hypothetical.** The high audio tone oscillator (`REG70/71`) and low CTCSS source (`REG07/51`) have emitted **simultaneously** on the same B-chip RF carrier in two independent physical TX16C recordings. Independently, `REG70` provides repeatable monotonic *FM-deviation gain* (TX15W) while preserving the recovered audio oscillator phase, and REG70 gating appears to leave the tone oscillator free-running (TX15V). A pilot-locked six-word `REG71` excursion has produced a repeatable **retained complex RF-carrier phase displacement** on four prospective TX15U RF controls/pulses. The independent raw DCS source also produces source-chosen two-level FM: TX16J delivered two independently received **40/40-bit** `RADIO` payloads at ~**50 bits/s** without MICIN or FSK FIFO. This is substantially stronger than the previous review, but **not arbitrary IQ**.

## Measured B-chip results

| Experiment | Evidence | What is actually proven | Unproven claim to avoid |
|---|---|---|---|
| **TX15S** | Three real RF captures; **114/114** heldout tones each; pilot-frozen programmed-FM RF phase coherence **0.936, 0.738, 0.826** vs carrier-only **0.416, 0.342, 0.361** | Source tone sequence predicts relative **complex RF carrier phase** better than no-modulation baseline | Generic RF I/Q DAC or calibrated spectral mask |
| **TX15U** | Two prospectively locked A controls and two B phase-steering pulses, **4/4 passed** fixed independent RF criteria; first exploratory TX15T set **3/4** (A02 failed) | Specific source-programmed short tone excursion yields measurable **retained differential RF carrier phase** | Arbitrary 0/90/180/270° phase state selection, absolute carrier phase |
| **TX15V** | Two conducted gate-period tests; audio-phase free-running model RMS **0.246°, 0.022°** vs freeze ~**101.7°**, reset ~**47.5°** | `REG70` output gate suppresses observable FM while internal oscillator **appears to keep phase advancing** | RF carrier phase DAC or absence of all analog transient effects |
| **TX15W** | Two physical gain-ladder runs, fixed 1091-Hz tone. FM deviation at gain 8/24/48/80 ~**848/1187/2035/4070 Hz**; recovered tone phase RMS **0.033°/0.156°** across heldouts | `REG70` changes the **FM deviation amplitude** monotonically and repeatably at **85-ms plateaus**, largely preserving recovered audio phase | Source *RF envelope* control, linear gain law, gain update bandwidth at 50/150/s or absolute calibrated deviation |
| **TX16A/B** | Two-run physical CTCSS frequency and gain demonstrations; TX16B gained 8-step deviation ladder | Distinct independently programmable **low-frequency FM** source | Signed arbitrary near-DC FM offset |
| **TX16C** | Two physical experiments; solo high ~**1186.5 Hz** FM deviation and low ~**561 Hz**, joint high ~**1186 Hz** and joint low ~**561 Hz** | Both oscillators **coexist on one RF TX**, independently gated at tested frequencies/gains | All frequencies/gains/rates or DCS streaming coexisting with CTCSS |
| **TX16D** | Two physical CTCSS gate-phase records; no frozen phase model met the threshold | Repeatable but currently **unexplained** CTCSS phase behavior | CTCSS free-run or reset phase continuity |
| **TX16F–H** | Physically emitted non-Golay raw 23-bit codes via DCS; TX16H globally free-running 134.4-bit/s clock better matches RF (**0.869/0.821** correlation) than reset/re-latch alternatives; at 48-ms source word changes **7/8 strictly qualified**, both runs | B-chip raw DCS word changes take effect **within frames** with a persistent source bit clock, not only at 23-bit boundaries | Infinite unframed payload or 134.4-bps user payload channel by default |
| **TX16I -> J** | Original `FLUX!` at nominal 20-ms intervals **failed 26/40** from accumulated ~0.605-ms-per-write cycle overhead; newly timed prospective `RADIO` **40/40 + 40/40** on independent conducted RF captures, zero errors | User-chosen 40-bit digital data can be emitted from **two raw DCS level words** at effective 50-bps source schedule | Robust BER across noise/durations; faster 100–200bps achieved; RX decodes `RADIO` blindly |
| **TX17C** | Real TX repeated **±50/+100** `REG38` low-word writes/readbacks, requesting **±500/+1000 Hz**; measured only ~**±5 Hz** | Under constant TXON the channel-frequency register **does not immediately retune RF** | A fast PLL frequency DAC through `REG38` writes alone |
| **TX20 APC** | Previously verified RF amplitude control stable around **80–100%**, ~94% retained 47.6-Hz contrast, ~40% 91-Hz; ~70% hysteresis and phase coupling | Slow **RF power-envelope** control distinct from `REG70` **audio-FM-deviation gain** | Simultaneous polar RF I/Q, high-rate linear QAM |

**Reference domains matter:** TX15S/U measured complex RF phase relative to a drifting RTL-SDR LO, while TX15V/W use audio-oscillator phase recovered from the FM discriminator. These are related but not identical metrics. TX16J's 50 payload symbols/s arise from controlled 20-ms REG08 updates; the DCS serializer's intrinsic 134.4-bps *chip* clock is not itself a demonstrated arbitrary continuous 134.4-bps user serial interface.

## Combining the controls: priority and limitations

1. **Proven already:** high-frequency FM tone plus low-frequency CTCSS FM, **both on the same carrier** (TX16C); and within the high-tone path `REG71` frequency + `REG70` gain/enable independently work, but *rapid combined changes* have not been qualified.
2. **Best immediate multi-actuator prototype:** measured tone frequency/gain + low CTCSS frequency/gain. First vary **one source at a time with the other held on**, score spectral/FM-discriminator component amplitudes, cross-modulation, RF carrier phase, and headroom. Then apply *known paired* control patterns with pilot-only calibration and independent IQ/spectrum holdouts. Avoid changing DCS mode at the same time until routing exclusivity is verified.
3. **Phase-state modulation potential:** combine calibrated `REG71` phase-steering pulse widths with gated `REG70` level transitions, but a four-symbol coherent differential phase alphabet has **not** been demonstrated. TX15U is just one successful A/B displacement under pilot calibration; TX15V gating does *not* conveniently reset phase.
4. **Digital data potential:** the raw-DCS all-zero/all-one two-level modulation can carry a 50-bps payload on RF without MICIN. TX16K currently explores 10/7/5-ms symbol dwells; this is ongoing uncommitted work, **not an accepted speed result**. Examine whether the high audio oscillator can remain enabled during DCS raw code output; TX16C **CTCSS** coexistence cannot be assumed to imply **DCS** coexistence, because the two subaudio modes may share registers/logic.
5. **APC + FM:** architecturally separate actuators, but a qualified physical *simultaneous* joint-polar TX has not occurred; previous remote tooling safety block must be honored. PA phase shifts, low-end hysteresis and insufficient bandwidth make APC low priority for coherent 50/150-symbol/s modulation.
6. **Direct synthesizer register `REG38`:** deprioritize direct live word writes; physical TX17C is a repeatable negative under TXON. Preserve a separately qualified state-re-latch / different offset register hypothesis, but don't keep repeating ineffective live writes.
7. **True arbitrary RF IQ:** none of the internal mechanisms yet provides a general signed per-sample DAC or two independent quadrature components. TX18 digital TX ingress and an external IQ daughterboard remain long-lead approaches. Using a *phase-predictive FM basis* plus APC may eventually approximate a limited polar constellation only if trained waveform errors and phase coupling meet strict, independently sourced complex-IQ target EVM.

## Recommended next tracked tests

**Top priority: TX15X — rapid fixed-tone `REG70` gain modulation**, with 50/150 updates/s, precommitted source clock, repeated reference pilots and terminal guard. Distinguish actual gain settling and RF phase disturbance from nominal 85-ms plateaus. Use separate physical runs and report phase, FM deviation transfer, spectrum and windowed IQ holdouts. This is the cleanest direct upgrade to a practical waveform synthesis backend.

**In parallel: TX16K — prospective faster raw DCS message at 10/7/5-ms bit commands**, already being developed **in the TX16 worktree**. Freeze scorer and message before emitting; do not count TX16I's retrospective 40/40 timing refit as an earlier RF success or equate DCS source rate with user payload rate.

**After those:** one *two-oscillator changing-gain* experiment to confirm simultaneous **dynamic** independent FM contributions (TX16C only tested fixed conditions); one predeclared **phase-state alphabet** test; and a possible **high-tone + raw-DCS** coexistence experiment after static work-mode audit.

Every RF test must retain bounded TX lease, known dummy-load/receiver fixture, exact firmware/module source, serial/SDR locking, preflight, no auto-retry, signed original module/VFO restore, and **pilot/holdout separation**. No unqualified RF I/Q or emission-mask claims.

## Reproducibility pointers

`research/native-fsk-pdm`:
- `docs/k6-tx15s-distributed-pilot-carrier-physical-20261122.md` (TX15S)
- `docs/k6-tx15u-independent-phase-steering-physical-20261122.md` (TX15U)
- `docs/k6-tx15v-gate-phase-physical-20261122.md` (TX15V)
- `docs/k6-tx15w-gain-physical-20261122.md` (TX15W; **untracked on the reviewed commit**)
- `docs/k6-tx17c-direct-synth-physical-20261122.md` (TX17C)

`research/tx16-subaudio-modulation`:
- `docs/k6-tx16c-two-generator-physical-20261122.md` (coexisting FM sources)
- `docs/k6-tx16d-ctcss-phase-physical-20261122.md` (CTCSS phase inconclusive)
- `docs/k6-tx16h-physical-20261123.md` (48-ms raw DCS updates)
- `docs/k6-tx16i-physical-20261123.md` (original 40-bit failure)
- `docs/k6-tx16j-physical-20261123.md` (twice independently decoded 40-bit message)

Raw IQ, qualification and hardware recovery archives for both branches reside at `/home/tyler/flux-bench/tx-injection-20261122/tone-oscillator/`.

**Status:** research assessment only; worktrees, live experiments and uncommitted source untouched.
