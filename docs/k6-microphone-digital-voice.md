# Flux microphone to MCU: digital-voice feasibility

Research worktree: /storage5/dev/flux-mic-digital-voice
Branch: research/mic-digital-voice
Parent: research/pa11-digital-audio (already validated receive audio on 0x4829).

## Verified and unverified

On the upstream original FD6818 (ID 0x4819), microphone audio was
measured on PA11 after removing the programming cable, entering the
chip's TX-audio state, disabling the power amplifier, enabling its
microphone ADC and analog AF output, and selecting an AF output mux.
The upstream 2026-11-21 micaf experiment reported roughly 33 dB
1300-Hz tone-to-neighbor contrast on select 4, absent when the mic
ADC was disabled. Upstream mickrec recorded several seconds of mic
audio in ADPCM without extra hardware.

Our FD6818B (ID 0x4829) has separately demonstrated that PA11 can
digitize live *receive* AF at about 17.5 kHz. The same microphone
routing has NOT been validated on FD6818B. Register maps, audio
mux selection, PA flag semantics and gain may differ.

The programming jack mechanically disconnects/mutes the internal
microphone while the cable is inserted. This prevents serial
recording and the internal microphone simultaneously. For a
hardware check, an installed module must work autonomously after
cable removal, retain audio in RAM, and report only after reconnection.
A microphone level detector REG_64 alone is NOT a sample stream.

## Candidate MICVOICE lab recorder

Source: c/k6/mod/micvoice.c adapted from upstream MIT mickrec, plus
micadc.h, cableout.h and the proven PA11 receive sampler.
Source has a deliberate compile-time safety gate:
FLUX_MICVOICE_LAB=0 by default. The normal module is inert and
returns -9. Never install or execute the enabled variant until the
test setup and register semantics have been checked.

The opt-in 0x4829-only, level-13 bench variant is designed to:
- Refuse an unexpected chip or VFO other than 440.850 MHz.
- Show PULL CABLE, require a full physical key press and release,
  then show TALK, THEN PRESS before the test.
- Check the channel energy, request a short bounded guarded TX,
  and identify with the configured research callsign AG7SU in CW.
- Call tx_pa(0), verify REG_36 PA-enabled indication is cleared,
  and only then attempt REG_30 mic ADC and AF DAC bits.
  Restricted fd_write is attempted first; the adapted upstream
  low-level bus write is a last resort and its value is read back.
  The RF chip is in a TX audio state even though the power
  amplifier is intended to be disabled.
- Use PA11's one-pin software ADC in that state, discard 1100
  settling samples, and record roughly 1 second as 4-bit IMA ADPCM
  in module RAM. The sampler checks the PA flag and exit key.
- Restore REG_30, AF mux, PA11 GPIO and speaker-amp state, call
  tx_off, and restore the original receive tuning.
- Display PLUG CABLE IN, wait for a physical key, then emit
  indexed ADPCM bytes and measured sample timing over serial.

The optional module uses no SPI flash or digital RF modem protocol,
and cannot automatically repeat a voice transmission. However,
tx_on may enable the PA before tx_pa(0) takes effect. The chip can
radiate leakage even after the PA is off. A correctly rated dummy
load is mandatory for initial validation. On a hardware failure,
code-level cleanup may not execute; the radio's independent TX
watchdog and physical power switch are separate safeguards.

This recorder is presently **code and emulator-validation only**.
No physical FD6818B MIC capture, no TX audio-mux verification, no
dummy-load/SDR recording, and no actual microphone WAV have
been obtained in this worktree. The last live serial status attempt
returned no response; no further device commands were issued.

Host parser: tools/k6_micvoice.py. It validates indexed ADPCM
rows, sample count, mic-bit state, end marker and timing, then
exports the unmodified compressed bytes, decoded raw samples and
an individually normalized 16-bit WAV. This is a diagnostics
decoder, not a speech encoder or real-time modem.

## Why ADPCM isn't the radio voice codec

At 8 kHz, 4-bit ADPCM is around 32 kbit/s, plus framing. It is
only a practical audio-path recording format, not a candidate for
the 600-baud eight-tone link (gross 3 bits/symbol = 1800 bit/s).
Pilots, sync, FEC, user ID and ACKs reduce that further. A
future speech codec will need hundreds of bits/s to perhaps
1000 bit/s of useful speech content, depending on the selected
modem and coding policy. The end-to-end bitrate must be measured,
not assumed from the symbol count.

The existing Sotto 1200 encoder performance numbers mainly
concern upstream original FD6818/M0 and selected Cortex-M4
optimizations, not a proven real-time encoder fitting the
FD6818B's available SRAM/CPU while running PA11 sampling and
a modulator. Codec2 ultra-low-rate modes and lightweight
fixed-phrase/data-plus-vocoder designs are alternative research
leads; a quality/speed/memory comparison is required first.

Possible architecture:
 microphone -> RF-chip TX audio ADC -> PA11 software ADC
 -> DC removal / pre-emphasis compensation -> speech encoder
 -> channel framing + FEC -> common digital TX backend -> RF.
Separate receive: GP0 IF or PA11 AF -> demod/FEC -> decoder -> PA11
speaker PWM. Half-duplex mode transitions must release TIM1,
GPIOA PA11, the RF-chip AF mux and PA ownership cleanly.

## Next physical gate

1. Confirm actual 0x4829 REG_30 / REG_36 bits, AF mux and current
   built-in independent TX watchdog with a radio firmware audit.
   Do not copy 0x4819 bit meanings without evidence.
2. Confirm a rated dummy load, local physical ability to remove
   and reinsert the cable, and a stable low-risk firmware with
   the necessary level-13 module API; no background TX work.
3. Run an initial short quiet/speech capture with the exact
   radio state confirmed and power amplifier off during MIC ADC.
4. Reconnect cable, decode the returned data; compare silence
   and spoken speech or a 1 kHz acoustic tone. Validate actual
   sampling rate, gain, clipping and passband.
5. Only after this, test longer clips and evaluate MCU speech
   codecs and a streamable modem. Do not merge experimental
   low-level RF-bus accesses into Flux main yet.
