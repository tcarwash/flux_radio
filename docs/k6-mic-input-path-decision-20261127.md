# K6 microphone input alternatives: noise-floor decision checkpoint

Date: 2026-11-27. Status: offline research / no hardware or radio changes.

## The current limitation is larger than the filter gains

The strong-generator **0.1 Vpp** GP0 captures at 500/1000/2800 Hz reconstructed through the actual firmware Rust decimator (offline, calibrated 7500/87260 ratio) yield about **16.53 / 17.06 / 15.10 dB** fitted tone-to-residual SNR for original boxcar. Generator-OFF output PCM RMS is **0.04231 of full scale**. The 7-tap late-divide prototype improves measured tone SNR only **+0.58 / +0.92 / +0.17 dB**, while AA3 improves low-frequency SNR more but **reduces 2.8kHz SNR by 1.57dB and tone amplitude by 4.61dB**. Source: `docs/evidence/k6-gp0-fidelity-sweep-20261127.json`, all based on CRC-verified real RF-captured GP0 group counts.

A first *illustrative* target of **25dB tone SNR** would require **7.94–9.90dB** improvement versus present original captures; **30dB** would require **12.94–14.90dB**. These thresholds are engineering goals, not a codec-specific proven intelligibility requirement. A 0.1Vpp generator is strong compared to many acoustic-mic signals, so real voice may have still less headroom. The user needed to speak loudly and heard prominent noise in decoded Sotto; source-side GP0 residual is already significant prior to encoding, and the vocoder may introduce additional artifacts. Do not equate receiver denoising with recovered information.

## Alternative path inventory grounded in previous physical K6 experiments

| Path | What we actually know | Can give clean signed PCM without opening K6? | Main obstacle / next evidence |
|---|---|---|---|
| **FD6818B MICIN → GP0 select 12 → PB1 → DMA** | Audio-dependent tone captured at 500/1k/2.8kHz, 64-bit group-count rate ~93kHz. Broad residual persists even with good 0.1Vpp input. | **Yes, but noisy at present.** | Find noise source and possible RFIC settings/clock readout improvement; narrow filters cannot remove in-band noise without speech loss. |
| **PA11** | Previous attended `MIC RAW`, `MIC ROUTES` and AF selection 1/4/5/9 investigations: sustained microphone tone **absent** after ~80–120ms, initial signal was RC startup transient. PA11 is speaker-PWM injection via RC filter. | **No, not with known wiring/routes.** | Would require new physical routing; repeating same PA11 capture won't help. |
| **PA0 = ADC1 channel 0** | Actual Aura board code uses PA0 to read **rectified VOX level**, not signed audio. The KD32F328 MCU includes a 12-bit ADC. | **No, with current analog wiring.** | Potential hardware route after schematic verification: isolated AC-coupled microphone audio to a free ADC-compatible node (or repurposed PA0) and careful bias/protection. This may sacrifice original VOX telemetry. **Do not electrically connect without design review.** |
| **PA1 = ADC1 channel 1** | Battery voltage sense. | **No, should not repurpose.** | Battery protection/monitoring must remain intact. |
| **RFIC I2S/MCBSP or other GP outputs** | Historic `i2sprobe` on 0x4819 found no usable I2S framing; only GP0 is wired to MCU PB1, whereas GP1/7 are not connected and others are occupied for control functions. Guide's 0x6818 register map differs from actual BK4819-compatible hardware. | **Unproven and unlikely software-only.** | Don't assume translated I2S guide defines a working FD6818B mic port; inspect chip-specific evidence and capture clock/data before a design. |
| **External ADC/codec → radio UART (PA10)** | A previous physical cable on this radio had 192kbaud bidirectional safe, **~9.5 kB/s PC→radio payload**. At 8kHz 8-bit raw PCM requires 8kB/s; 16-bit PCM 16kB/s exceeds that test, whereas 4-bit ADPCM needs ~4kB/s. | **Not a drop-in mic input.** | PA10 is also the **PTT** pin and shared programming interface; mic connector/UART ownership and real-time feed during TX require new safely framed protocol/lease. Existing UART benchmark does not prove simultaneous PCM/FSK or safe continuous PTT. External hardware/host required, defeats autonomous radio goal. |
| **External encoder/sidecar delivers Sotto packets** | Bypasses GP0 and its input noise, uses independent better ADC/microphone. | **For compatibility/testing, potentially yes.** | Needs a supported packet ingress/control and an external device; would be a useful reference against the on-K6 encoder, not an autonomous stock radio. |

Historical documents `docs/ht-survey.md` and the first paragraphs of `docs/k6-fd6818-audio.md` predate discovery of GP0 mic-dependent samples and claim no mic-to-MCU route. **That old categorical conclusion is superseded by the November 25–27 physical GP0 evidence.** The negative PA11 and I2S tests remain relevant.

## Decision-first test to isolate the problem WITHOUT opening the radio

Use the **same nominal 0.1Vpp 1kHz source** and a generator-output-OFF baseline with an independent SDR receiver and fixed gain, and compare:

1. **Analog FM TX path** into dummy load + coupled RTL-SDR, recover the 1kHz audio from the analog FM carrier, estimate tone frequency and tone-to-residual SNR with carefully fixed SDR gain, frequency deviation, limiter settings, ADC source and clipping. The operator must explicitly attend and physically key any required transmission. No unattended TX.
2. **GP0 RAW AIR path** using existing proven 86-packet diagnostic (or its already saved 1kHz physical capture if generator and setup are comparable). Decode CRC-valid groups and replay source through pinned Rust reference.
3. If **analog FM is substantially cleaner** than GP0 at the same mic input, suspect the RFIC's GP0 digital output or our 5.5MS/s GPIO read/decimator more than the mic analog circuit. Prioritize GP0 read-clock/selector, RFIC internal gain and modulation route controls offline before soldering.
4. If **analog FM is also poor**, prioritize analog microphone front end, jack/contact, bias, grounding, externally injected stimulus and actual speech levels; a new digital output off the **same** noisy RFIC analog front-end may not help.
5. This cross-path comparison is not perfectly matched because FM RF and demod add their own noise, gain and emphasis. Fixed-gain strong-signal SDR capture and matched post-filter measurement are important; don't treat audible 'clear tone' as numerical proof.

The GP0 mic-only route currently saves `[REG_30,0x47,0x48,0x7D]` and sets `REG_7D` low five bits to **31 (maximum of this field)**. More ordinary mic-gain setting increases cannot be assumed available, and physical PA11 gain-sweep results do not test GP0 SNR directly. A controlled offline REG_7D / input route hypothesis matrix may help identify **which path** handles gain/noise, but don't write RFIC registers blindly on a transmitting radio.

## Go/no-go criteria

Keep the current firmware and its prior safe SPI/module backups. Set an explicit engineering objective of **≥25dB measured source SNR at strong test signal**, **no more than ~1dB upper-speech-band attenuation**, and subjective speech intelligibility better than present Sotto output with a matched reference. This is a provisional bar, not a mathematical minimum for Sotto. A sustained **≥8–10dB reduction** in noise for the strong test input is more meaningful than the best present ~1dB FIR gain. If the analog FM / GP0 discriminator isolates a bad GP0 sample path and readout/clock fixes fail, evaluate a **direct MCU ADC mic tap** with a board-level schematic, wiring/pad inspection and protective bias/AC-coupling design. That is a separate explicit hardware-modification decision; don't open the radio, repurpose battery sense or remove VOX without consent.

No new transmission, module install, SPI access, or firmware alteration was performed to prepare this memo.
