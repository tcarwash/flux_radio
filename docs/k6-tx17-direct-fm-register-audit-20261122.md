# Flux TX-17 — FD6818B/BK4829 direct-FM synthesizer register audit

**Physical RX-only preflight (2026-11-22):** The K6 RX tunes at 162.550 and 162.475 MHz measured −93/−97 dBm, squelch closed, and a receive-only RTL-SDR scan did not verify a sufficiently strong steady reference. Both VFOs were restored to 144.390 MHz; **no register-latch experiment executed**, no transmitter enabled. See [TX-15Q clock report](k6-tx15q-clock-transfer-physical-20261122.md). Old 0x4819 non-B latch evidence still cannot be applied to B.

Checkpoint: November 22, 2026. **Read-only firmware source and
published documentation audit.** No register writes, RX retune,
RF emission, serial connection or flashing performed.

Goal: find a *serial-controlled* instantaneous **signed carrier
frequency-offset** actuator that might approximate an arbitrary
RF phase trajectory better than framed 2-FSK or audio tone FM.

## B-chip evidence and important non-equivalence

The physical K6 under test reported RFIC ID **0x4829**.
`firmware/k6-aura/build/aura-fd6818b-port/src/drivers/fd6818b.rs`
implements the newer B/BK4829 family, but old FD6818(0x4819)
register addresses and behavior cannot be copied without proof.
A recent public Baofeng UV-5RH reverse-engineering report also
proposes equivalence of FD6818B and BK4829 based on real
successful driver porting:

https://github.com/amoxu/Baofeng-UV-5RM-5RH-RE/issues/18

BK4829 datasheet (2023) states the FM modulator resides **in the
RF frequency synthesizer**, and its nominal reference crystal
is 26 MHz:

https://www.alldatasheet.net/html-pdf/2171272/BEKEN/BK4829/1433/8/BK4829.html

This establishes a *functional block*, not a publicly
documented low-latency signed-frequency register.

## Source-controlled register candidates

| Source / register | Observed B-driver role | TX-17 prospect | Current conclusion |
|---|---|---|---|
| `0x38/0x39` | Channel RF center frequency, `REG_FREQ_LO/HI` | Retune carrier | Older non-B real RX test found write latched until RXON; **B behavior untested**. Frequency changes may re-lock PLL rather than smoothly modulate |
| `0x3B/0x3C` | Reference/crystal frequency trim in older-family docs | Low-rate precision pull | Old-chip +24-step RX trial shifted +2035 Hz **only after RXON**, not when written in RX. B reset/latch model must be verified; not a proven fast control |
| `0x40` | `REG_DEVIATION`: enabled FM deviation / scale | Modulator magnitude | Changes modulation **gain**; does not encode the signed offset target or supply a modulation waveform itself. Dynamic B-chip effect may warrant a separate test after guards |
| `0x70/0x71` | Audio tone gain/frequency | Frequency control in audio domain | **Real working TX-15**; changes audio sinusoid frequency, not static signed RF offset |
| `0x07` / `0x51` | Subaudio CTCSS clock / decode | Slow analog FM source | Separate TX-16; low-frequency periodic modulation, no proven DC signed FM control |
| `0x59/0x5F/0x72` | Native FIFO 2-FSK mode/data/clock | Binary frequency modulation | Physically verified TX-10/FSK-* path; finite frames and heldout RF-IQ errors constrain arbitrary phase fidelity |
| `0x04` (legacy guide) | RX offset **calibration** enable | Unlikely | Another hardware description states RX offset calibration takes ~50 ms with RX on; not a credible fast TX frequency DAC |
| `0x7E` | Audio/baseband DC-related configuration | Uncertain | Do not treat an undocumented DC-cancel bit as a PLL DAC or write it on speculation |
| `0x73` | RX AFC control | Unlikely | Receiver frequency correction setting, not demonstrated TX frequency command |
| `0x36` | PA/APC target | Independent envelope control | TX-20 hysteresis/bandwidth physically measured; **not** frequency modulation or an unrestricted amplitude DAC |

Supporting in-project checkpoints:
[Old-chip crystal/tune latch experiment](k6-own-fsk.md),
[B-specific FSK research](k6-native-fsk-pdm.md),
[Measured tone oscillator FM](k6-arbitrary-rf-waveform-engine.md),
[APC envelope](k6-apc-fine-hysteresis-bandwidth.md),
[FD6818B reference calibration](k6-fd6818b-refcal.md).

A different multi-chip radio publicly documents **two-point FM**
from an *external modem and TCXO control line*:

https://radiofoundry.org/it/docs/spec/hardware-db25d

That is an important **TX-14/external-control** clue, not a
register/address/pinout proven compatible with this K6.
The K6 is reported to use a plain 26-MHz crystal with no
board-verified external fast modulation pin or digitally
driven varactor interface.

## Decisions / next discriminators

**No verified fast signed direct carrier-offset control register
has been found in the audited B-chip driver or cited public
datasheet.** The existence of the synthesizer's internal FM
modulation block does not imply the MCU can write its
instantaneous offset using an SPI register.

1. **TX-15P first:** test continuous audio oscillator phase
   across *non-commensurate* REG_71 changes with simultaneous
   heldout RF IQ; this preserves serial and is already a live
   47/48 heldout tone-ID path. Details:
   [phase vector](k6-tx15-phase-discriminator-20261122.md).
2. **TX-17A B-chip RX-only:** with a known independent
   RF reference and reversible attended bench, compare readback
   and receiver IF frequency on a single 0x38/39 write
   *without* RXON, then after RXON, restoring exact original
   values/center. Check B silicon identity 0x4829 and ensure
   the firmware owns retuning. Do **not** adopt the old
   non-B latch result as B evidence, or use a live writer
   competing with the serial catalog/UI.
3. **TX-17B state-aware reference trim:** only after a
   safe RX-only backup/recovery procedure is established,
   repeat small bounded 0x3B/0x3C trim on B receiver.
   Never write crystal calibration flash, PA registers or
   unknown modulation registers during an exploratory test.
4. **TX-14 two-point external reference:** map the *actual*
   B-chip K6 oscillator circuit and available test pads,
   then decide whether a reversible externally driven
   reference source can work without sacrificing serial.
5. Preserve TX-11 native multilevel/DMR search and
   TX-18 I2S/digital baseband as independent possibilities.

A clean RX-only register test may still find *retuning*,
which is not equivalent to a phase-continuous waveform
generator. A viable control must respond promptly, hold
carrier phase across updates or follow a separately measured
phase law, tolerate the expected update rate, and pass
measured complex RF holdout/SNR/occupied-bandwidth tests.

**No RF TX and no serial device touched for this audit.**
