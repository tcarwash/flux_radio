# TX-15X — rapid REG70 FM-deviation gain modulation

Status: **TX-15X stimulus and guarded bench frozen; scorer v2 is frozen for
run02 after one exploratory run exposed an RF-onset alignment offset**. The
first run remains exploratory and is not an independent pass. This tests the
next unsampled modulation vector after the steady-state TX-15W gain ladder; it
is not an arbitrary-I/Q or RF-power test.

## Frozen source schedule

Target is FD6818B ID `0x4829`, existing experimental firmware build
`95d6b260d4122287`, 433.500 MHz, guarded 1.8-second TX lease and continuous PA
on the operator-confirmed dummy load. The only changing RFIC register is
REG70 gain; REG71 remains `11264` (nominal 1091-Hz tone). No PLL, APC, mic,
I2S, calibration, or MCU firmware writes.

The source starts with a gain-24 pilot plateau, then runs 24 alternating
gain-16/gain-64 symbols at each tick period, in this order:

| Block | Tick period | Scheduled rate | Duration |
|---|---:|---:|---:|
| A | 200 × 100 µs | 50 symbols/s | 480 ms |
| B | 100 × 100 µs | 100 symbols/s | 240 ms |
| C | 67 × 100 µs | 149.25 symbols/s | 160.8 ms |

A second gain-24 plateau follows. RF begins after a 60-ms tone setup; the
modulation and pilot sequence is scheduled for 1.2208 s, plus register I/O,
inside the independent 1.8-s watchdog lease. Every update is read back before
the source waits for its absolute module-tick deadline. The source restores
original REG70/REG71 before dekeying and reports success only after all three
blocks complete.

Exact enabled source hash: `77ce5f2c90743c05678b827cb22aa114aa41934e41817b4a14b8ae0255839e93`
(640-byte module). Default source is RF-inert. The host bench pins that exact
hash, requires explicit dummy-load confirmation, checks chip/build/VFO/channel
and SDR preflight, saves the signed slot-30 module, performs one SPI install
and one TX request, records IQ, then verifies module/VFO restoration. It does
not retry.

## Frozen scorer and interpretation

Run01 was first evaluated with a fixed RF-onset-to-symbol offset; this gave
the expected high/low order at 50 symbols/s but inverted it at 100 and 149.25
symbols/s. An exploratory timing sweep of the unchanged saved IQ found a
common **−8.5-ms** offset that recovered high-minus-low contrast of **1.428,
1.429 and 1.209** times the gain-24 pilot deviation at the three rates. The
50-symbol/s block is now used only to estimate this timing offset; it is
training and cannot independently qualify itself. Frozen scorer v2 applies
that one offset unchanged to the 100- and 149.25-symbol/s holdout blocks.

`tools/k6_tx15x_gain_dynamics_analyze.py` fits the 1091-Hz discriminator pilot
frequency and phase from the initial static gain-24 window only. It estimates
one alignment offset from the first alternating block and measures each
symbol's FM-discriminator tone amplitude and phase after a fixed 1-ms edge
exclusion. Synthetic positive, negative, and known-offset controls pass before
run02. The scorer measures the SDR's composite FM-discriminator response,
not calibrated RF power, direct RF-carrier phase, or complex RF amplitude.

For run02, predeclared heldout positive evidence requires the 100- and
149.25-symbol/s blocks to show gain 64 above gain 16, with contrast at least
0.10 of the gain-24 pilot deviation after applying the block-A-only offset.
Report all three rates' audio-phase RMS and full symbol values regardless of
amplitude outcome. Since run01 is exploratory under the revised alignment,
run02 alone cannot establish repeatability; a further independent capture
would be needed. Occupied spectrum and coherent complex-IQ trajectory remain
separate qualification gates.

Emulation/scorer tests validate timing, guards, restoration, synthetic
positive and negative controls. They do not predict physical switching
bandwidth or authorize conclusions from register readback alone.
