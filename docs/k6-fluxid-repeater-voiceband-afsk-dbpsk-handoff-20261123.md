# Flux Repeater ID: voice-band audio phase / AFSK feasibility handoff

**Research handoff, 2026-11-23 PST.** For the actively running Flux K6 modulation experiments. **Do not interrupt existing TX-15 / TX-16 tests**; this is a *new queued research vector*, not a request to key a radio now. Keep the existing serial/SDR session, uncommitted files and RF fixture undisturbed.

## Outcome

Enable two Flux-equipped ordinary FM radios to exchange a speaker's **self-asserted callsign and optional Maidenhead grid** through an *unmodified analog voice repeater*, then display the decoded metadata while permitting normal voice QSOs. No Internet link, repeater decoder plugin, or dedicated digital-voice repeater should be required.

**Critical architectural observation:** standard analog FM repeaters demodulate the incoming RF, process the recovered **voice-band audio**, and re-FM-modulate a *different RF carrier*. Input RF absolute/carrier phase, power envelope, and subaudible CTCSS/DCS payload generally **do not** pass transparently. In-band **audio waveform frequency and *relative audio phase*** may survive, within a repeater's audio passband, group delay, limiting, AC coupling, AGC/companding, pre/de-emphasis, clipping, noise and access/kerchunk gating constraints. Not all repeaters have the same audio path, and some use nonstandard/link/flat audio routing. Therefore: measure an actual repeater chain before interoperability claims.

The word **“silent”** must be qualified. Data transmitted in the repeater voice passband can be heard on ordinary FM receivers unless sufficiently low-level/masked; Flux can locally suppress decoded bursts from *its own speaker*, but cannot mute third-party listeners. Lower injected signal level also lowers decode reliability. Do not advertise an inaudible overlay without listening tests and measured decode-vs-audibility tradeoff.

## Why the other discovered transmit methods differ

- **TX-15U** physically measured intentionally **retained complex RF-carrier phase displacement** from six REG71 frequency writes. This demonstrates controllable FM phase *integral*, **not** an RF phase state surviving a repeater and **not** yet a selectable **audio** BPSK phase shift. Do not just replay TX-15U waveform and call it DBPSK.
- **TX-15P/Q/R/S/V/W** do physically show source-programmable **audio-frequency** tones, pilot-corrected recovered audio phase, oscillator phase apparently **free-running under REG70 disable**, and a monotone phase-preserving *audio FM deviation gain* controlled by REG70. This makes an **audio phase-excursion test** plausible.
- **B-chip driver provides an explicit Bell 202 AFSK configuration**: `firmware/k6-aura/build/aura-fd6818b-port/src/drivers/fd6818b.rs` `afsk_config_tones()` writes **1200 Hz to REG71 and 2200 Hz to REG72** (this register also performs other roles in different RFIC modes); `afsk_set_tone()` chooses the active source using REG70 enable/gain. Its hardware TX through an actual repeater is **not** qualified merely because those functions exist. TX15 has already demonstrated frequency-controlled voice-band tone sequences on RF.
- **TX-16C** physically combines 1100-Hz main oscillator with 100-Hz CTCSS **simultaneously** on the same FM RF carrier; two independently qualified captures at tested gains. A strong candidate for preserving **CTCSS access continuously while a pre-voice voice-band data burst** plays. Static coexistence does not prove that a live microphone can be mixed in simultaneously.
- **TX-16J/K/L** built raw-DCS all-zero/all-one signed two-level FM *without* using MICIN or the normal FSK FIFO; **50, 100, 143, 200, and 250 payload bit/s** on short prospective chosen-data RF captures, but 333/500 bit/s failed. This waveform is in the **subaudible** FM deviation path: most ordinary repeaters reject or regenerate it and will not copy its raw DCS word stream to the voice output. Do not equate successful direct-RF DCS with repeat-through capability.
- **Latest TX-16M side-thread note (unmerged, read from separate worktree):** DCS `REG51[13]` polarity inversion of a constant all-one DCS code produces two signed-FM levels with **one register write per symbol**, positive alternating-source RF spectral results down to **2.5ms commanded dwell** in two captures; 1.25ms not qualified. **No independently decoded arbitrary faster packet** from that vector yet, and still subaudible. It may be valuable for direct-RF data modes, *not a voice-band repeater metadata solution*.
- **TX-20 APC** controls the RF envelope, which is regenerated/lost by the repeater. Its measured limited ~80–100% range, bandwidth and phase pulling make it unsuitable as the first repeater data carrier.

## Two competing voice-band physical TX modes

### Path A — robust in-band audio FSK: baseline / must implement first

Use the internal tone generator to transmit **two frequencies within the ordinary FM voice passband**; start with **1200/2200 Hz** because the B driver has Bell 202-related code, or use already-validated REG71 single-tone updates if the REG72 dual-tone route turns out incompatible. Keep modulation depth conservative and measure amplitude/settling/audio filter rolloff from physical IQ.

Do **not** assume the B-chip can produce a 1200-baud Bell 202 modem merely because two oscillator frequencies are configured. The verified TX15 mode was ~150 register updates/s; fast register writes, dual-tone mode, symbol timing, aliasing and occupied spectrum need separate physical qualification. Start with **25/50/100 symbols/s**; increase only when a frozen decoder and capture prove acceptable errors. Use GFSK/continuous-phase transitions or short matched-filter tone detection if timing/spectral constraints demand it.

Use a **short, pre-voice packet**, after allowing a configurable repeater access/key-up delay. Keep CTCSS stable. Verify a second **receiver path** separately: other Flux unit or raw SDR IQ -> FM audio discriminator -> digital decode, without ground-truth payload leaking into the decoding fit.

### Path B — **audio DBPSK** via tone oscillator frequency-excursion: promising but unproven

Hold a midpassband oscillator near **1500 Hz**. Encode a binary *differential audio phase* toggle by programming a brief **+/- frequency excursion** for a duration chosen to accumulate an extra half cycle before resuming 1500 Hz:

`Delta_phi_audio ≈ 2*pi * integral( f_commanded(t)-f_reference, dt )`.

Illustrative only: a **+75-Hz excursion lasting 6.67ms** ideally produces **180°** relative audio phase. This is about one TX15 source slot at nominal 150/s; existing tests measure ~149.6/s *relative to the SDR* and nonzero analog transition delay. **This numeric example is not a calibrated waveform or ready-to-TX command.** Actual frequency words/intervals must be quantized, measured and compensated *using reference/pilot only*; oscillator reset at changed REG71 words has not been uniquely eliminated by physical audio observations in all conditions.

A possible use of REG70 enable gating is to suppress transition artifacts while the oscillator continues advancing. TX15V measured free-running **audio** phase across gain-enable OFF; gating may introduce **audio dropouts and AM sidebands** that degrade DBPSK or audibility, so compare *ungated* vs *gated* excursions, not assume gating helps.

**Minimum physical discriminator:** 0/180° versus 0/0° control waveforms, same initial tone, pilot-conditioned differential audio phase recovered from the **RF discriminator**, independently held-out symbols and multiple captures. Report phase distributions, cycle slips, timing drift, audio amplitude/sidebands, and symbol BER; **do not use actual RF carrier-phase displacement as evidence of audio DBPSK**. The repeater model must separately be fed the recovered voice-band audio, not the complex RF carrier IQ.

### Expected result from the repeater

An FM voice repeater may cause **constant or frequency-dependent audio phase shift and fixed/group delay**. A DBPSK receiver can ignore a constant phase rotation; changes within/across symbols, PLL jitter, audio gates and nonlinear clipping can still break it. DBPSK is **not guaranteed** to be less audible than AFSK or more robust through the repeater; this is what the same-packet comparison should measure.

## The RF / audio-channel test ladder

1. **No live hardware:** take existing saved TX15 complex IQ and recover audio with a well-specified FM discriminator. Apply an offline channel simulator to known audio reference waveforms: (a) baseline clean/direct; (b) 300–3000-Hz bandpass with explicit low/high cutoff sweeps; (c) pre/de-emphasis and varied group delay; (d) limiting/compression/noise, receiver oscillator drift, varying delay, and hard mute/key-up truncation; (e) second FM remodulation/demodulation with *independent* RF carrier phase/frequency. Receiver must work from output audio without knowing the original RF carrier phase. Treat filter parameters as sensitivity sweeps, **not** assertions about every repeater's exact frequency response.
2. **Physical direct-RF baseline:** use **guarded, operator-confirmed dummy-load RF** and the existing signed-module-slot and VFO restore fixture; calibrate AFSK 1200/2200 Hz (or tested alternatives) at 25/50/100 baud with a precommitted pilot and **new heldout callsign/grid payload**. Capture raw IQ and voice audio; replay through offline repeater channels. Firmware source, receive filters and score thresholds must be frozen before RF. Do not run while the other TX16 experiments own serial/SDR.
3. **Independent audio-DBPSK demonstration:** transmit predeclared audio phase pulse patterns and measure actual retained AUDIO phase across word transitions using pilot-only source-clock calibration. If this fails, stop instead of mistaking TX15U carrier-phase steering for success. Only after it passes attempt data symbols and a new heldout payload.
4. **Physical over-the-air repeater, only with permission/coordination:** choose a known permissive FM repeater, confirm its CTCSS/DCS access, talk-in audio delay, reverse/forward audio processing and any legal local policy, and record its output on an independent second radio/SDR. Maintain genuine access signaling throughout; do not assume repeaters retransmit incoming subaudio or that hang time keeps the receiver unmuted. Evaluate packet BER/PER, audio quality, squelch behavior, spectral occupancy and clip/loss vs packet placement.
5. **Voice overlay** is a *later hardware milestone*. The verified tone TX mode sets `REG30=0xC3FA` (MICIN ADC disabled), unlike normal analog voice state `0xC1FE` (MICIN ADC enabled). **No physically verified MICIN+internal audio tone simultaneous TX** exists yet. Until proven, send a discrete packet followed by normal MICIN voice; do not promise seamless audio or “silent under speech” merely because TX16C mixes two **internal** generators.

## One protocol shared by both modulation paths

Build a codec independent of modulation: **`FluxID v0`**, framed data with version, packet type (speaker-ID), length/flags, callsign, **optional** 4- or 6-character Maidenhead grid, sequence or short nonce, and CRC16. Start with a compact fixed **ASCII or restricted callsign alphabet**, not an unverified bitcount; include sync/preamble, training, interleaving/FEC only when the chosen code rate and measured repeater path justify them. Preserve a **single exact packet bitstream** for comparing AFSK vs audio DBPSK.

Receive behavior: reset old sender on a new QSO; display decoded callsign and optional grid with **“unverified/on-air claim”** semantics unless an independent authentication layer is actually implemented. CRC rejects corruption but **does not authenticate a callsign**. Respect user preference to omit grid/location and avoid automatically rebroadcasting private metadata; the grid is opt-in.

Keep traditional required station identification via legally permitted methods. **Do not assume a low-level nonstandard digital metadata packet satisfies U.S. amateur station identification**, and do not interfere with shared repeater use. For normal radios with no Flux decoder, perceived audio chirp and interference remain relevant usability metrics.

## Definition of done for the first research milestone

- Two independently specified packet payloads and a third **previously untested heldout** payload, encoded once and sent through AFSK in captured voice-band audio, with frozen preamble/timing/CRC decode, resulting BER/packet success stated explicitly.
- A/B receiver test on the *same packets* under several channel processing cases, including a second regenerated RF carrier with randomized input vs output carrier phase.
- A specific **prospective** demonstration or falsification of audio 180° phase selection. Report *audio* phase RMS and differential symbol recovery, not only modeled RF IQ coherence.
- At least two real-RF repeats per accepted TX mode, with original raw IQ hashes, independent software verification, module/VFO and TX lease restoration, and measured relative occupied spectrum/audio audibility.
- Explicitly state what is **not yet verified**: actual repeater compatibility, MICIN voice mixing, inaudibility, other ordinary receivers decoding Flux frames, legal ID substitution.

## Source/evidence links in this local checkout

- `docs/k6-tx15s-distributed-pilot-carrier-physical-20261122.md` — pilot-frozen audio and complex RF phase characterization.
- `docs/k6-tx15u-independent-phase-steering-physical-20261122.md` — carefully **RF-carrier-phase** phase steering, four prospective qualified validations.
- `docs/k6-tx15v-gate-phase-physical-20261122.md` — REG70 gate off preserves ongoing oscillator **audio** phase.
- `docs/k6-tx15w-gain-physical-20261122.md` — two physical gain/deviation curves; this file was untracked in this worktree at handoff time.
- `docs/k6-multi-actuator-tx-review-20261123.md` — main and TX16 cross-branch status.
- `/storage5/dev/flux-tx16-subaudio-modulation/docs/k6-tx16c-two-generator-physical-20261122.md` — 1100-Hz + 100-Hz combined FM *physically verified*.
- `/storage5/dev/flux-tx16-subaudio-modulation/docs/k6-tx16l-highrate-physical-20261123.md` — 250-bps chosen subaudio-FM success, 333/500-bps negative.
- `/storage5/dev/flux-tx16-subaudio-modulation/docs/k6-tx16m-polsrc-physical-20261123.md` — more recent separately staged single-register 2.5-ms polarity source positive; the independent new data payload remains pending.

**Handoff to the active agent:** Add this to the *new* repeater-audio research track; do not displace TX15X/16M or edit their in-progress test source. The most discriminating next engineering investment is a shared **in-band packet + offline two-carrier repeater-channel harness** and a tiny **audio DBPSK phase-excursion pilot**, alongside the conventional AFSK baseline.
