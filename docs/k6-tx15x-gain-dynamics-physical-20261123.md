# TX-15X — rapid REG70 gain updates remain visible at 149 symbols/s

**Status:** two independent RF captures scored with the same frozen v2
pilot-trained analyzer; conducted dummy-load experiment; repeatable gain
deviation control established through the tested ~149-symbol/s pattern.
This is one more measured modulation vector, not arbitrary RF-IQ generation.

## Stimulus and safety

The K6 FD6818B (ID `0x4829`) ran firmware build `95d6b260d4122287` at
433.500 MHz. The known REG71 tone word `11264` stayed fixed. The only
modulation updates were alternating REG70 gains 16 and 64, in 24-symbol
blocks at 50, 100 and 149.25 symbols/s. Initial and final gain-24 pilots
bracketed the sequence. Each run used one guarded 1.8-second TX lease, a
dummy-loaded PA, air-coupled RTL-SDR IQ, and one SPI module install/run/restore.
No MCU flash, calibration, APC or synthesizer writes were made.

Both firmware executions returned result 3 after all three rate blocks.
Both receipts show the exact original slot-30 executable restored, both VFOs
returned to 144.390 MHz, and radio TX idle. Enabled module executable SHA-256:
`77ce5f2c90743c05678b827cb22aa114aa41934e41817b4a14b8ae0255839e93`.

## Scoring correction and held-out evidence

The first pass with a fixed RF-onset timestamp scored an incorrect high/low
order in the faster blocks. A post-run exploratory sweep of run01 found a
common −8.5-ms alignment offset. That first capture is therefore exploratory;
its revised score is not counted as a prospective confirmation. The scorer
was then frozen in commit `c101503`: it estimates one timing offset from the
50-symbol/s block, fits tone frequency/phase only from the initial gain-24
pilot, and applies the same offset to the faster 100- and 149.25-symbol/s
blocks. The two captures below were both scored by this unchanged v2 method.

| Measurement | Run 02 | Run 03 |
|---|---:|---:|
| Timing offset estimated from 50-symbol/s block | −8.25 ms | −8.50 ms |
| Pilot tone estimate | 1090.9304 Hz | 1090.9297 Hz |
| 50 symbols/s high-minus-low / gain-24 pilot | 1.427 | 1.428 |
| 100 symbols/s high-minus-low / gain-24 pilot | **1.426** | **1.426** |
| 149.25 symbols/s high-minus-low / gain-24 pilot | **1.317** | **1.215** |
| 100 symbols/s recovered audio-phase RMS | 0.90° | 0.74° |
| 149.25 symbols/s recovered audio-phase RMS | 1.88° | 1.54° |

The predeclared held-out criterion required positive high-minus-low contrast
of at least 0.10 pilot units at both faster rates. Both runs pass by a wide
margin. At 149.25 symbols/s, gain contrast remains 85–92% of the steady
TX-15W gain16-to-64 separation (1.428 pilot units). This is strong evidence
that REG70 gain can shape the *FM frequency-deviation envelope* at the tested
update rate. It is not RF power envelope control.

Run01 raw IQ SHA-256:
`2e58bb89f04af2c23c5c70e1bc543bdd29be7533f46bedf5ba022bec37323547`.
Run02:
`835550a87b657aeeb0bd8f0d211192f1dbc4a9b692759ed7e0154f31c4d17b09`.
Run03:
`dc0206894e1f6096bbf24deb1143499cc5ab06d58529ef5a8146c0bf42045382`.
Run02 and run03 module completion/restoration receipts and frozen v2 score
JSON are in `/home/tyler/flux-bench/tx-injection-20261122/tone-oscillator/`.

## Limits and next vector

Phase residuals are for the recovered 1091-Hz FM-discriminator audio tone,
not complex RF carrier phase. The SDR is air-coupled and uncalibrated; there
is no TX power or emission-mask conclusion. The trial proves only three
rates and one gain alphabet, not a global gain transfer function, arbitrary
deviation trajectories, or repeater survival.

The next high-value phase-vector experiment is TX-15Z: test prospective
differential phase-state targets using the already measured tone-frequency
and gain/enable actuators, with a pilot-only prediction and independent
complex-RF IQ holdout. TX-15X supplies a useful fast deviation-control path
for that work but does not by itself provide phase-state control.
