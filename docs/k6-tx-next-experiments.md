# Flux K6 — TX waveform research: next experiments

**NEW TX-24B / FSK-03D OFFLINE-ONLY NATIVE FSK + REG40 COEXISTENCE EXPERIMENT (2026-11-23):** [Frozen known-sign 8-frame, 33-word guarded protocol](k6-tx24b-reg40-native-fsk-offline-prospective-20261123.md), unranked [GitHub queue #26](https://github.com/tcarwash/flux_radio/issues/26). Holds `REG70=0x0095` FSK gain and `REG72=0xC194` 4800 baud *fixed*, varies only REG40 lowbyte between frames **16/40/16/80/16/00/40/80**, then measures constant-sign FSK ±separation in 256 plateau bits/frame (2048 total). No fast REG40 switching; this is deliberately **distinct from Antigravity-owned TX-26 `REG70` per-symbol 4FSK** and does not claim 2 bits/symbol. The default-inert API3 module, exact signed-slot/VFO restoring single-TX harness, and public-marker-only receiver pass synthetic/emulator controls; receiver independently located all **8/8 public sync frames** of a prior FSK13 real B-chip SDR recording (not a new TX or REG40 effect). **No TX24B RF** as of preparation; P0 [#9] has a hardware request, so all physical access is deferred pending a separate explicitly ACKed new K6/SDR lease and canonical lock.

**TX-24 / FSK-03C PHYSICAL POSITIVE (2026-11-23):** [Frozen prospective B-chip REG40 deviation RF evidence](k6-tx24-reg40-physical-positive-20261123.md), with [queue #17](https://github.com/tcarwash/flux_radio/issues/17) and verified shared hardware release. Exactly one independently archived conducted 433.500-MHz dummy-load RF transaction, 13 fixed 90-ms REG40 low-byte changes with constant REG70/71, finished module result 13; signed slot30 and both original VFOs restored. The original pilot-only, eleven-heldout scorer froze at 5acbd7b BEFORE physical RF and **passed all five locked criteria**. Normalized RF-FM deviation levels lowbytes 00/16/40/80 are **0.92123/1.00023/1.15125/1.38039**, matching the old-family (256+level)/(256+22) law under four B-chip measurements. A separate posthoc raw-discriminator-RMS method independently agrees within 0.001 and received complex RF magnitude stayed within ~1%; this is modulation deviation control, NOT PA power amplitude or arbitrary RF IQ. **Next**: TX-24B isolated/native FSK-mode compatibility and four-level target under a newly frozen protocol, without disturbing the FSK-13/14 finite-voice transport; no simultaneous-FSK co-modulation has been physically tested.\n\n**PRIOR OFFLINE MODULATION VECTOR TX-24 / FSK-03C (initial development, before physical test):** [REG40 B-chip FM-deviation investigation](k6-tx24-reg40-modulation-vector-offline-20261123.md) ([shared GitHub queue experiment #17](https://github.com/tcarwash/flux_radio/issues/17)). The B-specific driver uses REG40=0x3516 and a separate lower-deviation configuration while scrambling; a new default-inert 13-plateau test changes only REG40 low8, holding REG70/71 fixed. A pilot-only heldout FM-discriminator scorer and guarded Cortex-M0 source pass **24 focused offline tests**. **At that original offline checkpoint the B-chip RF response was untested; the later independent physical qualification is documented above**. It must not preempt the P0 FIFO or live-DV bench, and a later FSK sign×gain composite does *not* establish native 4FSK without independently measuring four frequency levels. Also audited TX audio coefficient banks REG74/44/45 and voice-scramble REG31 as lower-priority source-path controls; neither is an arbitrary-IQ sample port.

**NEW HIGH-PRIORITY NATIVE-FSK STREAMING + DV TRACK (2026-11-23):** [FSK-09S / FSK-10 / FSK-11 / FSK-12 test roadmap](k6-fsk-streaming-packet-dv-roadmap-20261123.md). Split **true B-chip FIFO in-frame refill / transparent 4800-bps symbols** from the independently achievable **packetized, jitter-buffered digital voice** over repeated finite frames, and the **store-and-forward Sotto voice pager** fallback. Physical FSK-09 repeated two 1024-bit frames with **46.719ms nonpayload interval** and one RF PA, implying **~3.94kbps illustrative gross repeat-frame rate** *if it scales*, not measured application goodput or gapless streaming. Both 512-bit heldout regions were decoded; the older-family presumed almost-empty IRQ did **not** appear on B silicon and refill safely aborted. A new [B-chip TX source audit](k6-fsk09s-b-chip-source-audit-20261123.md) plus [two status-only RF observations](k6-fsk09s-status-probe-physical-20261123.md) found no repeatable occupancy/headroom signal; `REG_0C` only marked frame completion, and the `REG_02` boundary value differed between repeats. No refill was attempted. Further direct-FIFO work now needs a B-specific source/interface lead for transparent mode or a documented occupancy flag. An initial [FSK-10 offline capacity/deadline model](k6-fsk-dv-model-20261123.md) estimates feasible payload geometry but is **not** packet-decoder, jitter-buffer, prerecorded-voice, or RF proof. Continue with a pilot-frozen new-payload packet decoder/jitter-buffer bench for finite frames. Explore **separate FSK + DCS/CTCSS/gain** source co-modulation only after RFIC work-mode compatibility audit and precommitted independent RF receiver validation; current simultaneous coexistence proof is *tone oscillator + CTCSS*, **not native-FSK + another actuator**. Never treat gross 4800 raw bps or tentative joint bitrates as a proven continuous user payload.

**TX-15V new physical REG_70 gate-phase finding:** [two independent conducted RF captures](k6-tx15v-gate-phase-physical-20261122.md) toggled REG_70 OFF for 57/71/83/97 ms with constant REG_71=11264 (1091 Hz) and continuous RF PA, measuring FM discriminator audio phase only after re-enable. Pilot-trained phase predictions over three heldout ON windows: **free-running oscillator 0.246° / 0.022° RMS**, versus frozen **101.66° / 101.68°**, reset **47.52° / 47.52°**. ON amplitude about 1187 Hz vs OFF ~54 Hz RMS; all 9 register writes and slot/VFO restoration verified each time. **REG_70 gates modulation but apparently leaves oscillator phase free-running**; does not reset initial oscillator phase or prove carrier IQ, but is a new phase-preserving modulation gate. **TX-15W** subsequently measured the static REG_70 gain curve. **Next: TX-15Z differential phase-state targets** after the fast-update experiment.

**TX-15X new physical fast-gain vector:** [two independent v2-scored conducted RF captures](k6-tx15x-gain-dynamics-physical-20261123.md) alternated REG70 gain16/64 on a fixed REG71 tone at 50, 100, and 149.25 symbols/s. Both held-out faster-rate blocks retained positive gain64-over-gain16 deviation contrast (1.426 and 1.215–1.317× the gain24 pilot) with recovered audio-phase RMS below 1.9°. The first run remains exploratory because its scorer was revised after initial onset-alignment error; runs02/03 both use the frozen scorer. This is FM deviation envelope control, **not RF power or complex RF-IQ amplitude**. **Next: TX-15Z differential phase-state target experiment** using tone-frequency and gain/enable actuators, pilot-only phase prediction and independent complex-RF IQ holdout.

**TX-17C physical B-chip synthesizer result (repeated negative):** [two independent guarded RF captures with original code/VFO restoration](k6-tx17c-direct-synth-physical-20261122.md) accepted and immediately read back six low-word 0x38 changes during continuous TXON, but **+500/−500/+1000 Hz requested steps produced only +1.7/−0.7/−4.5 Hz in run01 and +4.8/−2.4/−2.0 Hz in run02**. The SDR carrier was strong (~25x noise). **No immediate RF-frequency latch effect**, so serial REG_38 alone is *not* a fast carrier-frequency DAC on this B-chip. RXON/TXON relatch untested. Preserve this negative finding and stop treating 0x38 readback as RF modulation. The TX-15V [gate/phase memory test](k6-tx15v-gate-phase-protocol-20261122.md) subsequently found pilot-predicted phase after enable gating consistent with a free-running oscillator; next high-value tone path is TX-15Z differential phase-state selection. Do not lose TX-20 APC, TX-18 digital baseband or TX-14 external clock paths.

**TX-17C B-chip direct-synthesizer live-latch discriminator (new protocol, not yet physical RF):** [separate, hash-pinned conducted-load experiment](k6-tx17c-direct-synth-protocol-20261122.md) prepares a single 1.26-second bounded TX with six 0x38 low-word edits (0 / +50 / 0 / −50 / 0 / +100 counts nominally 0 / +500 / 0 / −500 / 0 / +1000 Hz), **without any RXON or TXON between writes**. The real RTL-SDR complex IQ is scored by an independent, predeclared 35-ms settling-excluded median-frequency estimator with synthetic positive and latched-negative controls. It is not sufficient merely to read back 0x38. The radio is otherwise restored exactly; all other phase/APC/I2S paths stay active. This directly addresses the previously unverified B behavior behind the TX-17 RX-only NOAA reference blocker.

**TX-15U prospective breakthrough — deliberate retained RF-carrier phase steering:** [full physical A/B evidence, old negative control and independent validation](k6-tx15u-independent-phase-steering-physical-20261122.md). In exactly **six** source-selected B-chip REG_71 tone writes, the 40-ms B pulse differs from unchanged A control, then returns to the *same* nominal tone and near-original oscillator phase. The original precommitted [TX-15T protocol](k6-tx15t-phase-steering-prospective-20261122.md) had **3 passes and 1 failed A control**, retained as a real negative result. A pilot-only effective audio-oscillator rate correction was then developed on those **four** old captures and committed as `16c9641` **before** four fresh RF emissions. The unchanged A/B firmware and previously locked qualification thresholds then scored **4/4 prospective passes** (two A controls, two B pulses). New B03 phase step: **+93.8° pilot-predicted, +90.1° measured**, correct/wrong source RF-phase coherence **0.964/0.521**. New B04: **+69.3° predicted, +74.5° measured**, coherence **0.976/0.522**. A03/A04 controls: **−1.4°/−1.5°** measured, coherence **0.938/0.530** and **0.855/0.533**. Each was real complex RF SDR IQ, 192/192 tone writes, zero firmware late, exact module/VFO restore. Strong physical evidence for **pilot-conditional retained RF carrier phase displacement**, NOT an arbitrary-IQ DAC, absolute open-loop phase-angle command, or qualified QAM/OFDM. Next: **TX-15V requested differential phase-state selection**, plus independent TX-17 B-chip synthesizer RX-only latch and TX-20 APC/polar paths.

**NEW physically held-out TX-15S RF-carrier phase evidence:** [three real distributed-pilot 192-event dummy-loaded B-chip transmissions](k6-tx15s-distributed-pilot-carrier-physical-20261122.md), all with 114/114 heldout tone IDs, 20 strong pilot references spread over four 18-symbol windows, six predeclared non-scored shutdown guard symbols and verified slot-30/VFO restore. With pilot-only timing fit, heldout changed-tone *audio* phase RMS is **8.11° / 5.36° / 4.95°**. Separately a pilot-fitted quadratic carrier-offset drift + continuous-FM source model predicts **measured complex RF-carrier phase** with heldout coherence **0.923 / 0.724 / 0.826** (carrier-only baseline **0.416 / 0.342 / 0.361**). **Model was frozen in commit `fcb28b7` before the independent third RF capture;** quadratic drift was chosen after exploring the first two. In run 3 a post-hoc inverted-FM diagnostic has coherence 0.035, vs source-correct 0.826. This is **actual received RF phase prediction of a constrained FM waveform**, a stronger result than audio-phase alone, **NOT independently programmable arbitrary RF IQ**. Preserve TX-17 signed synth latch and TX-20 amplitude alternatives. Next: TX-15T intentional RF-phase steering, safe TX-17 RX reference.

**Latest physical TX-15R longer-pilot result:** [two real 192-word guarded K6 RF bench captures](k6-tx15r-long-pilot-physical-20261122.md) use 48 prefix-pilot symbols and 144 heldout, including 15 strong pilot transitions and 44 heldout strong transitions. The radio returned 192/192, late=0 twice, with exact slot-30 code and original VFO restoration. Heldout audio tone IDs decoded **143/144 and 144/144** (both terminal slots have poor RF-shutdown-conditioned fits). Same-word phase RMS **0.383/0.374°** over 95 transitions; changed-word phase RMS **13.79°/30.35°** after frozen pilot clock/lag correction over 48 transitions. Cross-run residual correlation **0.884** and 18.5% recovered audio-deviation rolloff from 502 to 2519 Hz. A pilot concentrated at the beginning fails to track late timing drift in run 02. **Next TX-15S:** predeclare distributed pilot windows + terminal unscored tail guard; preserve all independent TX paths. No coherent arbitrary carrier IQ demonstrated.

**TX-15Q update — major corrected physical finding:** [real RF SDR-relative symbol-clock/phase reanalysis](k6-tx15q-clock-transfer-physical-20261122.md). Both saved TX-15P captures reveal that a fixed *nominal* 150/s scheduler assumption caused much of the apparent changed-tone phase discontinuity. A **pilot-only** clock fit from three large transitions cuts 17 heldout edge timing median error from 634/467 µs to **18/20 µs**. Audio-phase RMS at heldout changed-tone updates falls from 110/101° to 17.7/17.9° after clock correction, and **9.4/12.7°** after one extra pilot-only effective audio-phase-delay fit. This is **SDR FM-discriminator audio**, *not* real carrier-phase IQ EVM, and pilot size/synthetic midpoint bias remain constraints. **TX-17**: RX-only NOAA reference was too weak to qualify a B-chip synthesizer latch test; both VFOs restored, no TX.

**NEW physical TX-15P evidence (two independent RF captures):** [real B-chip tone phase and repeated-write discriminator](k6-tx15p-physical-phase-evidence-20261122.md) transmitted the new 64-event non-commensurate waveform twice through the user-confirmed dummy-load bench with the serial controller and SDR. Both 64/64 modules completed with zero late writes and full slot-30/VFO restoration. Held-out tone IDs decoded 48/48 in both captures. The 28 held-out same-word transitions had only **0.424° / 0.404° audio phase RMS** apparent discontinuity, while 20/19 qualified changed-word transitions had ~110°/~101° RMS. This is **FM-discriminator audio phase, not carrier IQ phase**. It rules against reset-on-every-write for unchanged words; frequency changes remain a major unknown. No arbitrary IQ achieved.

**Serial-preserving TX direction:** [TX-15P non-commensurate oscillator phase vector](k6-tx15-phase-discriminator-20261122.md) fixes a hidden flaw in the previous 150-update/s tone experiment: every old tone had an *integer number of audio cycles per symbol*, so 47/48 heldout tone-ID decode could not distinguish oscillator phase restarts from continuous operation. A new opt-in 64-word vector, 16-pilot/48-heldout and same-word rewrites separates three ideal phase hypotheses (pilot coherence ≤0.701, heldout ≤0.332). Its original design was first emulator-tested; **later TX-15P/Q/R live RF results are documented above**.

**Direct carrier-offset candidate audit:** [TX-17 B-chip synth/FΜ register evidence](k6-tx17-direct-fm-register-audit-20261122.md) finds no demonstrated fast signed frequency-offset register. Known B-driver registers tune the channel, set deviation gain, generate audio tones, or operate framed 2-FSK. A cautious independent B-chip RX-only 0x38/39 latch test is next; old non-B results do not settle B silicon. This research does not require MICIN access.

**Additional independent RX negative control:** [TX-12D2 strict spectral gate and pilot-frozen 4-FSK holdout](k6-micin-strict-spectral-rf-qualification-20261122.md) integrates the physical 2-ms RF-edge detector: it sees the historical **26-ms** (+22.55-dB relative spectral) PA-off pulse and refuses to interpret it as 44-ms four-level modulation. The separately developed 48-kHz/raw-U8 observer remains independently usable. Both are software-only; the actual MICIN adapter is still unverified.

**Newest physical-fixture gate and offline observer:** [TX-12D MICIN jack mapping, host audio hardware inventory and saved-IQ receive-only qualifier](k6-micin-physical-fixture-preflight-20261122.md). The unconnected 3.5-mm ring / shared PTT-programming sleeve requires real electrical verification, while an archived PA-off MICIN probe shows a separate 26-ms RF-like spectral transient. The strict 44-ms 4-FSK receiver rejects it as NOT qualified modulation. No new RF TX or flash was attempted.

**Latest independent TX-actuator feasibility:** [TX-12B loopable MICIN voltage pattern and hypothetical AC-coupling bench](k6-micin-loopable-coupling-20261122.md) reduces a 128-symbol stimulus to a 44-ms repeatable cycle inside the 100-ms experimental burst. Simulated 20-Hz first-order HPF causes 18/32 errors on long-plateau stress data, but 0/32 on short-term balanced data. This is NOT measured MICIN RF; the adapter and audio/RF synchronization remain unverified. The separate [TX-12C emulator acceptance](k6-micin-api26-emulator-acceptance-20261122.md) passed all eight applicable compiled-image tests (one optional feature skipped).

**Latest phase-response transfer evidence:** [FSK-25 leave-one-recording-out finite-memory phase study](k6-fsk-phase-transfer-20261122.md) predicts held-out chip-boundary phase from *other* recorded pilots. Seven-tap coherence is ~0.70–0.83 on three repeats, ~0.24 on fourth; linear drift extrapolated from pilot made results worse. Data decoding is not coherent arbitrary-IQ synthesis.

**Newest joint-actuator research:** [TX-21 offline polar/IQ feasibility and phase-coupling evidence](k6-polar-combined-iq-offline-20261122.md) fuses two *independent* real RF captures to an explicitly counterfactual amplitude-only EVM of ~4.5%; a separate APC-only phase-fit analysis finds directional phase perturbations around amplitude edges. **No simultaneous FSK+APC RF was transmitted.** Preserve the physical joint experiment as a future validation gate, not an achieved feature.

**Newest measured results:** [TX-20 lower APC hysteresis and amplitude rate response](k6-apc-fine-hysteresis-bandwidth.md): four further dummy-loaded K6 RF transmissions (two 13-step, two fast-envelope) confirm stable 72% on descent, 70% initiating repeatable approximately 8-ms deep RF fades, and return-to-stability by 80%; high-side 80-100% contrast is 94-95% at ~47.6 Hz but only 39-40% at ~91.1 Hz. **Experimental firmware and all temporary modules were restored to the original API26 production build, 31 catalog entries verified.** The [earlier 7-step APC experiments](k6-apc-envelope-measured-followup.md) and all other TX paths remain recorded below.

Updated 2026-11-22. Research branch: **research/native-fsk-pdm** at
/storage5/dev/flux-native-fsk-pdm. This is the persistent **all-path TX
research queue**. Preserve experimental alternatives even if a particular
native-FSK approach fails.

**Program objective:** [arbitrary complex RF waveform engine](k6-arbitrary-rf-waveform-engine.md) with separate phase/frequency and envelope paths; FSK is **one source of modulation control**, not the entire objective. The new polar-IQ workbench models arbitrary complex targets and explicitly identifies missing RF envelope and phase-rate capabilities.

**Scope and safety:** Earlier milestones were host-only. As of 2026-11-22,
the user-confirmed dummy-load/air-coupled SDR bench has supported real,
firmware-bounded 433.500-MHz native FSK transmissions and offline analysis.
The newest repeated-capture work performed **three additional short
native-FSK transmissions** and restored both VFOs after each one.
The SDR is not a calibrated spectrum analyzer; no unrestricted on-air
or spectral-mask claim follows. No firmware was flashed in this pass.

**TX16N high-rate polarity-FM payload checkpoint (separate worktree):** the
64-bit `N0CALL73` long-payload source has passed twice at 2965 μs requested
dwell (fine preamble fit 3.185 ms) using a 48-symbol alternating timing
preamble and frozen held-out scorer. A 2962 μs request was mixed (one 64/64,
one 62/64); 2960 μs was also mixed (one 64/64, one 61/64); 2945 and 2915 μs
requests failed. See the local detailed report
at `/storage5/dev/flux-tx16-subaudio-modulation/docs/k6-tx16n-run-v21-2965-long-preamble-physical-20261123.md`.
These are bounded conducted binary-FM payload results, not repeater survival,
spectrum compliance, or arbitrary complex IQ; no repeater was keyed.

Follow-up boundary point **TX16N v23 at 2964 μs** also failed the exact
payload gate in both independent captures (61/64 and 62/64; same 3.180-ms
fine-grid fit, recurring errors at bits 24 and 52). This sharpens the current
repeatable boundary to 2965 μs, without changing the wider arbitrary-IQ goal.
Full evidence and IQ digests:
`/storage5/dev/flux-tx16-subaudio-modulation/docs/k6-tx16n-run-v23-2964-long-preamble-physical-20261123.md`.
Next, pursue a distinct modulation vector from the roadmap rather than more
repeats at this failed setting.

**TX16M single-register DCS polarity actuator completed:** two independently
captured conducted runs verify repeatable opposite FM levels and an alternating
waveform at 10, 5, and 2.5 ms requested dwells; 1.25 ms remains exploratory
because the recovered deviation collapses. This establishes a fast binary-FM
source, not selected-payload BER or arbitrary RF IQ. Preserve the physical
report at
`/storage5/dev/flux-tx16-subaudio-modulation/docs/k6-tx16m-polsrc-physical-20261123.md`.
The next high-impact path is a pilot-frozen new-payload packet decoder and
jitter-buffer bench for finite native-FSK frames; direct FIFO refill remains
unproven after the B-chip status-only observation.

**Latest independent non-FSK-injection evidence:** [real B-chip programmable-tone oscillator RF test](k6-arbitrary-rf-waveform-engine.md): 64 audio-tone updates at 150/s through the PLL FM path, zero firmware-reported late updates, **47/48 held-out tone IDs** using training-only timing. This bypassed the native-FSK FIFO. The next priority is a safe [REG_36 APC/envelope response study](k6-arbitrary-rf-waveform-engine.md), not only more FIFO work.

**Latest native-FSK evidence:** [2026-11-22 repeated 8-FSK/PDM captures, binary-chip decoding and phase FIR analysis](k6-fsk-pdm-repeats-and-phase-20261122.md). Four captures have **0/1,792 heldout native-chip errors**, **0/112 popcount-reconstructed symbol errors**, and **5/112 direct analog 8-FSK symbol errors**. A training-only FIR greatly improves heldout chip-boundary phase prediction. The [earlier gain sweep](k6-tx-injection-live-evidence-20261122.md) remains separately reproducible.

## What has been established

- The FD6818B chip on the tested K6 identifies as **0x4829**. The existing
  hardware native-FSK test transmitted a binary waveform successfully.
  Known-payload-assisted recovery of archived SDR IQ gave 0/1024 errors at
  1200 bits/s, 3/1024 at 2400, and 30/512 at 4800 for particular captures.
  At 2400 the three errors were in bits 0,1,3; at 4800 all 30 were in the
  first 168 bits. These are **not independent blind BER measurements**;
  preamble/acquisition rather than sustained data failure is a hypothesis.
- Flux's B driver uses REG_3F for modem mode, REG_59 for FSK TX enable,
  REG_5F for the FIFO, REG_72 for FSK timing and REG_70 for tone/FSK gain.
  REG_40 is also used for FM deviation. **REG_70 gain now has measured
  native-FSK separation data for six codes**, while REG_40's exact
  native-FSK effect on this B silicon remains unverified.
- The original non-B chip's REG_3B/REG_38 tuning writes were observed to
  latch until RXON/TXON. This rules them out as fast phase-continuous RF
  modulation on that tested radio; the B chip must not be assumed identical.
- An independent API-26 MICIN hardware investigation confirmed bounded
  module TX can enable MICIN ADC (REG_30 0xC1FA to 0xC1FE) with PA off
  and toggle/restore REG_2B TX filter bypass. **No external shaped MICIN
  analog waveform has yet been injected**; an isolated jack adapter is
  still needed for real analog 4-FSK.
- The FD6818 programming guide's suggested I2S register layout conflicts
  with the tested 0x4819 and 0x4829 maps. Its I2S/MCBSP and DMR examples
  remain architectural leads, not a safe B-chip register recipe.

Sources within this repository: [native FSK research](k6-native-fsk-pdm.md),
[MICIN lab](k6-micin-bypass-lab.md), [digital TX backends](k6-digital-tx-backends.md),
[original FSK research](k6-own-fsk.md), [RF lab](k6-rf-lab.md),
[FD6818 audio](k6-fd6818-audio.md). Original known-payload IQ files:
**/storage5/dev/flux-serial-throughput/serial-test-output/tx-matrix/**.

## One investigated route: binary native 2-FSK as an RF frequency DAC

Given two instantaneous RF offsets -D and +D at the native bit rate,
a carried first-order error accumulator selects binary states whose
time-average tracks a desired frequency trajectory:

    average offset ~= D * (2 * fraction_of_high_bits - 1)

This is a **constant-envelope, phase-trajectory** experiment, not an
arbitrary RF I/Q or amplitude DAC. Average frequency alone is inadequate:
complex-IQ phase fidelity, spectral occupancy and the actual silicon's
switching/PLL response determine whether the waveform is useful.

Earlier ideal simulation with D=4000 Hz badly polluted the spectrum
for eight tones from -1050 to +1050 Hz. Reducing modeled D to
1100–1200 Hz improved the spectrum, and an ideal phase-coherent RF comparison
was much more favorable at 4800 native bit/s than at 2400 bit/s.
**This is a model, not a calibrated 0x4829 capability.**

**Reproducible 32-symbol eight-level sample (seed 17, 75 symbols/s,
1.6-kHz complex-IQ comparison filter):**

| Native bits/s | Assumed deviation | Complex coherence | Normalized EVM | Ideal 99% occupied BW |
|---:|---:|---:|---:|---:|
| 2400 | +/-1200 Hz | 0.275 | 3.50 | 5.10 kHz |
| 4800 | +/-1200 Hz | 0.844 | 0.64 | 5.69 kHz |
| 4800 | +/-4000 Hz | 0.058 | 17.19 | 15.32 kHz |

These are sequence/filter-specific ideal-model results, not RF power,
interoperability, or actual K6 deviation measurements. A 99% occupied
bandwidth number is not a regulatory spectral-mask pass.

### Offline implementation on this branch

- **tools/k6_native_fsk_pdm.py:** original per-symbol encoder preserved;
  new pdm_encode_frequency_samples accepts *one desired frequency per native
  FSK bit*, with its phase-error accumulator maintained continuously.
- **tools/k6_fsk_waveform_bench.py:** seeded four- and eight-level FSK,
  Gaussian-smoothed eight-level trajectories, chirps, an MSK target at
  600 symbols/s, and optional phase pulses. It compares complex RF IQ
  after the *same* reference/approximation filter: coherent correlation,
  normalized EVM, coherent/in-band power, wrapped phase error, 99% occupied
  bandwidth and power outside +/-3 kHz. It emits JSON + NPZ research
  vectors; it writes **no radio-loadable packet or device command**.
- **tests/test_k6_fsk_waveform_bench.py:** deterministic vectors,
  bounds/phase-quantizer checks, ideal waveform fidelity regression,
  frame-capacity flag and no-hardware CLI smoke test.

Run reproducible host-only tests:

~~~sh
cd /storage5/dev/flux-native-fsk-pdm
python3 -m pytest -q tests/test_k6_native_fsk_pdm.py tests/test_k6_fsk_waveform_bench.py
python3 tools/k6_fsk_waveform_bench.py \
  --out-dir /tmp/flux-fsk-pdm-waveform-study \
  --modes 4fsk,8fsk,gaussian-8fsk,chirp,msk600 \
  --native-bauds 1200,2400,4800 \
  --deviations-hz 1100,1200,1400,4000 \
  --symbol-baud 75 --symbols 32
~~~

The report marks any vector that would exceed the existing experimental
64-word FIFO frame. An actual streaming/refill API has **not** been built.
The phase-pulse experiment is **not** verified BPSK; MSK targeting is also
not proof of interoperable transmitted MSK.

### Native 2-FSK experiments, ordered

| ID | Experiment | Evidence required | State |
|---|---|---|---|
| FSK-01 | Sweep modeled deviations, native clocks and desired waveforms; compare complex-IQ reference, not just mean frequency | Seeded vectors, coherence/EVM, occupancy and bandwidth | Implemented offline |
| FSK-02 | Reanalyze archived SDR IQ for phase continuity, edge settling, frequency separation, clock drift and startup errors | [Offline FSK-02 report](k6-fsk-02-archived-iq.md): receiver I/Q filtering improves 4800 baud from 28 to 6 assisted-timing errors; phase reset unproven | Initial archive analysis complete; physical validation remains |
| FSK-03 | Characterize gain/deviation REG_70, REG_40 and FSK control/clock | [Physical RF gain sweep](k6-tx-injection-live-evidence-20261122.md) confirmed ±1,050 to ±2,100 Hz at gain16–48; pilot-hold deviation around ±1,200 Hz at gain21 | **REG_70 characterized on this 0x4829; REG_40 native-mode effect still unverified** |
| FSK-04 | Test long 000/111 runs and 0101 transitions, measuring instantaneous RF deviation and cumulative carrier phase | [Repeatable pilot holds and transition](k6-fsk-pdm-repeats-and-phase-20261122.md) show ±1.2 kHz, ~156–172 µs receiver-filtered edge, small extrapolated phase bridge | **Initial real-IQ edge test; full phase continuity and calibrated instrumentation remain open** |
| FSK-05 | Design guarded known-vector FSK laboratory module | [Known-vector lab](k6-fsk-05-known-vector-lab.md) compiled/emulator-tested; a later FSK-capable build runs the guarded 64-word module with confirmed cleanup; [repeat capture evidence](k6-fsk-pdm-repeats-and-phase-20261122.md) | **Real hardware verified for finite 64-word frames; continuous streaming unresolved** |
| FSK-06 | Distinguish startup and symbol-clock recovery from steady native-bit errors | Four 4800-bit/s RF captures independently acquired from 320-bit known pilot: [0/1,792 heldout chip errors](k6-fsk-pdm-repeats-and-phase-20261122.md) | **Observed with fixed payload; variable sequences, SNR and blind synchronization remain open** |
| FSK-07 | Compare physical 300-symbol/s PDM 8-FSK and underlying 4800-chip/s modem | [Four RF captures](k6-fsk-pdm-repeats-and-phase-20261122.md): 107/112 direct frequency-level symbols; 112/112 via chip popcount; transition-aware FIR improves heldout phase predictions | **8-level encoded information established; waveform RF EVM/OBW and other symbol rates remain open** |
| FSK-08 | Try MSK/GMSK, shaped FSK, chirps and phase pulses; reject if fidelity/adjacent RF energy fails | [FSK-24 real IQ holdout](k6-fsk-physical-iq-holdout-20261122.md) finds poor coherent waveform reproduction despite correct chips; [FSK-25 transfer](k6-fsk-phase-transfer-20261122.md) finds reproducible short-memory response on 3/4 captures. New payload / independent clock control needed | Conditional; neither information decode nor chip-boundary coherence establishes arbitrary-IQ TX |
| FSK-09 | Investigate transparent FSK mode, FIFO refill/chaining and preambleless operation | [Physical two-frame and negative FIFO-IRQ bench](k6-fsk-09-fifo-boundary.md): two 64-word frames decode 0/1,024 heldout bit errors with one TX lease but insert **46.719 ms** of extra modulation; 128-word AE-refill test safely aborted twice with REG_02=0x0800 rather than expected 0x4000 | **Frame boundary characterized; transparent streaming NOT established; B-specific FIFO IRQ mapping remains open** |
| FSK-09S | Prove true **gapless FIFO streaming** through 0x4829-specific status/occupancy/IRQ audit and qualified small refill | [Streaming/DV roadmap](k6-fsk-streaming-packet-dv-roadmap-20261123.md), [source audit](k6-fsk09s-b-chip-source-audit-20261123.md), and [two status-only RF captures](k6-fsk09s-status-probe-physical-20261123.md): no repeatable headroom flag; direct refill remains prohibited pending a B-specific transparent-mode or occupancy source lead | **HIGH PRIORITY; UNPROVEN; do not retry old-family IRQ assumption** |
| FSK-10 | Packetized **near-real-time Sotto DV** using finite native-FSK frames + receiver jitter buffer | [Offline capacity/deadline model](k6-fsk-dv-model-20261123.md) covers FIFO fit, assumed framing/repetition, and erasure deadlines; still require actual compressed-frame packet encode/decode, pilot-only frame reacquisition, new-payload CRC/FEC, RF-confirmed net goodput, buffer behavior and playback underruns | **HIGH PRIORITY; model partial, packet/PHY integration open** |
| FSK-11 | **Store-and-forward Sotto voice messages** over bounded 64-word FSK packets | Host-encode Sotto 1200/600/400, sequence/CRC/reconstruct audio, actual RF capture and round-trip, partial/retry handling; 10s of 1200bps voice is 1500B before protection | **FALLBACK; BURST PHY EXISTS, VOICE TRANSPORT UNTESTED** |
| FSK-12 | Secondary modulation-control path **alongside native 2-FSK** (REG51 DCS polarity / CTCSS / REG70 gain / tone) | Static chip-mode/register conflicts, one-source controls, **real simultaneous** RF IQ with independent two-channel decoder and FSK BER/spectrum. Tone+CTCSS coexistence is not FSK+subaudio coexistence | **UNPROVEN; separate safe work-mode audit before any joint TX** |

**Decision:** A narrower ideal RF spectrum does not imply correct RF
phase. If silicon deviation cannot be reduced or phase continuity/
framing cannot be maintained, do not promote PDM to a deployable backend.

## Do not lose these additional TX entry points

These are **independent hypotheses**, not variations of one implementation.

| ID | Entry point | Why it matters | Next discriminating check |
|---|---|---|---|
| TX-10 | Native **ordinary direct 2-FSK** | Already functional baseline for packet modes, no frequency dithering needed | Characterize rate/deviation, frame overhead, repeatability, power |
| TX-11 | Native **4-FSK/DMR/dPMR/transparent modem** in B/BK4829 | [Read-only B-driver source audit](k6-tx11-multilevel-source-audit-20261122.md): present TX controls describe binary framed FIFO, while 4FSK/DMR functions are RX DSP; no documented native B-chip four-level TX mode yet | Find B-specific documentation / stock TX code, prove actual four RF levels; do NOT conflate DMR RX or analog MICIN with native symbol FIFO |
| TX-12 | **MICIN baseband** with TX pre-emphasis, 300-Hz HPF, LPF bypass | Experimental API-26 MICIN enable was **compiled but not flashed**; only RX register/filter restore was physically tested. [TX-12B](k6-micin-loopable-coupling-20261122.md) provides a 44-ms loopable waveform; [TX-12C](k6-micin-api26-emulator-acceptance-20261122.md) rebuilt API26 and cleared its emulator regression (**8 passed, 1 skipped**) | Verify physical mic-pin/bias/voltage and isolated adapter; independent hardware TX/watchdog readiness and timing; then conducted 1-kHz and balanced/stress four-level RF measurements |
| TX-13 | **Filtered MCU PWM or external DAC physically connected to MICIN** | Software-generated analog frequency-control waveform; alternate to chip modem | Map actual K6 mic jack and bias; safe reversible adapter. **PA11 currently drives speaker path, not MICIN** |
| TX-14 | **TCXO/reference modulation and two-point FM modulation** | Could enable slow direct-offset trajectories beyond AC-coupled MICIN | Identify K6 board oscillator and accessible modulation pad; bench-only reversible electrical validation |
| TX-15 | **Tone oscillator REG_70/71, dual-tone/AFSK** | [TX-15P](k6-tx15p-physical-phase-evidence-20261122.md) and [TX-15Q](k6-tx15q-clock-transfer-physical-20261122.md) establish heldout audio-tone phase behavior; [TX-15U](k6-tx15u-independent-phase-steering-physical-20261122.md) demonstrates pilot-conditional retained RF-carrier phase displacement; [TX-15W](k6-tx15w-gain-physical-20261122.md) measures the static gain curve; [TX-15X](k6-tx15x-gain-dynamics-physical-20261123.md) repeats gain contrast through 149.25 updates/s | Multiple tone/deviation control vectors measured; arbitrary carrier-IQ synthesis, phase reset, spectral qualification, and repeater-surviving TXID remain open; next TX-15Z differential phase alphabet |
| TX-16 | **CTCSS/DCS/subaudio modulation source** | Potential slow signed FM injection or separate programmable path | Trace summing point, control clock, gain, deviation and achievable polarity |
| TX-17 | Undocumented **direct FM offset / synthesizer DAC** | [B-chip register/source audit](k6-tx17-direct-fm-register-audit-20261122.md) finds no verified fast signed offset register; 0x38/39 carrier tune and 0x3B/3C reference trim might latch, 0x40 is gain | **RX-only B-chip latch experiment**, with source/IF clock reference and restoration, before risking any TX test |
| TX-18 | **I2S/MCBSP or digital baseband / GPIO** | Maybe RX PCM or TX data stream without analog mic | Old FD6818 register addresses mismatch B; only GP0/PB1 wired to MCU in known board; confirm map and direction |
| TX-19 | **REG_3B/38/39 static LO/xtal tuning** | Carrier trim and maybe very slow hops | Proven latched on original non-B; determine B behavior before considering anything beyond static calibration |
| TX-20 | **PA/APC envelope and true CW gating** | [Four further measured RF experiments](k6-apc-fine-hysteresis-bandwidth.md): FD6818B APC hysteresis around 70-80% and high-side AM contrast retained 94-95% near 48 Hz, only 39-40% near 91 Hz. Existing [7-step evidence](k6-apc-envelope-measured-followup.md) still applies. All experimental firmware reflashed back to original after testing. | Instrument **actual register-write timestamps**, RF amplitude attack/release, conducted spectral purity and calibration; avoid 70-76% until instability understood |
| TX-21 | **Polar TX / simultaneous frequency and RF amplitude** | [Independent live FSK/APC capture fusion, APConly phase perturbations](k6-polar-combined-iq-offline-20261122.md): two counterfactual IQ compositions yield ~4.5% **amplitude-only** EVM, explicitly **not transmitted**. APC-only 80/100% transitions show repeatable local phase-fit offsets (~+0.14/+0.22 rad descending and ~−0.28/−0.26 ascending across two captures), source attribution uncertain. | Need a real, separately validated **simultaneous native-FSK+APC RF capture**, training-only pilot alignment, heldout complex EVM, spectral check and full restore. [TX-21B pilot-only heldout IQ verifier](k6-polar-iq-heldout-verifier-20261122.md) now generates independent 1024-bit FSK plus APC 80/100% target and synthetic regression coverage; NO real joint RF. [FSK-24 real archived FSK-only complex-IQ holdout](k6-fsk-physical-iq-holdout-20261122.md) exposes ~107–171% full-IQ heldout EVM despite reliable native chip decoding: digital information recovery is NOT coherent arbitrary-IQ synthesis. The remote tool safety gate blocked staging a joint TX module in the earlier checkpoint; do not bypass |
| TX-22 | **First-party pluggable TX backend contract** | [Generic arbitrary-IQ polar workbench](k6-arbitrary-rf-waveform-engine.md) now reports **empirical FD6818B APC suitability**: 80-100% floor, target envelope energy above 50/90 Hz, measured lab contrast and an ideal-clamping EVM lower bound alongside idealized waveforms | Define synchronizable frequency/APC control backend, real GPIO/register timing and measured complex RF EVM; do **not** treat a lab-feasibility report as emitted IQ |
| TX-23 | **External DAC/daughterboard/transverter/SDR gateway** | Honest fallback for real I/Q, conventional SSB/QAM, narrow FT8/WSPR, or high-fidelity phase modulation | Board access, RF filtering and independent conducted validation |

Possible protocol families for whichever physical entry point succeeds:
binary CPFSK, MSK/GMSK, 4/8/16-FSK, M17-like 4FSK,
narrow weak-signal FT8/FT4/WSPR/JS8 **only if** oscillator precision,
deviation and stability meet their requirements, chirps, and experimentally
phase-keyed waveforms. **Constant-envelope binary FSK cannot directly
produce arbitrary amplitude-varying SSB/QAM/OFDM.**

### External implementation and hardware references

- BK4819/UV-K5 family: https://github.com/egzumer/uv-k5-firmware-custom
- FD6818B/UV-5RH reverse engineering:
  https://github.com/amoxu/Baofeng-UV-5RM-5RH-RE/issues/18
- Another B driver: https://github.com/sophiel-meow/bf-5rh-firmware-open
- BK4829 driver reference: https://github.com/armel/uv-k1-k5v3-firmware-custom
- Original FD6818 guide (not a B-chip register map):
  https://www.glaver.org/blog/UV-17/FD6818.pdf
- MICIN flat-mod precedent on **non-B** chip:
  https://github.com/mobilinkd/uv-k6-digital-mod
- TCXO/two-point precedent:
  https://radiofoundry.org/docs/spec/hardware-db25d

## Shared bench gates for all future physical TX trials

1. Review source diff and firmware hashes; verify a usable radio-recovery
   path before experimental flashing. Do **not** merge an uncontrolled TX mode.
2. Require explicit operator consent, legal amateur test frequency,
   confirmed 0x4829 identity, correct TX lock and low-power configuration,
   independent hardware/firmware timeout, and an external abort.
3. Operate **conducted only** into a rated 50-ohm dummy load, suitable
   coupler and verified attenuation to the SDR. **PA-off is not RF-off**.
4. Preserve and restore register/calibration baseline on every path,
   including aborts; never copy original non-B values to the B chip.
5. Record exact SDR IQ sample-rate, frequency calibration, waveform seed,
   bit and phase timing, input power, RF deviation, spurious/adjacent
   emissions, harmonics, firmware commit and post-test RX restoration.
6. Start with short bounded *known* vectors. Assisted-data correlation
   is diagnostic, not unbiased bit-error or interoperability proof.
7. Only promote a backend after repeatable fidelity, decoder success,
   emission and independent watchdog evidence.

## Milestones

- **M0:** offline frequency-per-chip PDM and complex waveform bench,
  tests and preserved TX roadmap (current).
- **M1:** archived IQ phase/settling and long-run analysis, source-only
  B-chip register/deviation audit. Initial archival results and filter comparison
  now documented in [FSK-02](k6-fsk-02-archived-iq.md); native-B phase
  continuity, exact deviation and true hardware startup remain open.
- **M2:** guarded arbitrary-FSK FIFO API in emulator, with tight bounds
  and independent timeout proof.
- **M3:** attended conducted short-pattern test **only after** all safety
  gates; capture calibrated RF I/Q and compare with modeled target.
- **M4:** build a reusable TX backend only for paths that meet the
  measured RF and stability requirements; continue investigating
  MICIN, TCXO and multilevel paths regardless.

Keep IDs stable; record each future experiment's branch, evidence path,
hash, revision, result and stop/go decision without overwriting earlier
failed or superseded experiments.
