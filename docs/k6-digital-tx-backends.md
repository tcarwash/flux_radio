# FD6818B digital transmit backend research

Branch: research/digital-tx-backends. Initial candidate is emulator-only.
No physical transmission, radio flash, shared API, or CW worktree change occurred.

The already-existing Flux module API has three relevant TX facilities:
- level 3: guarded tx_on/tx_off/tx_pa and RF-chip writes
- level 11: fixed 16-byte native FSK hardware transmission
- level 13: bounded Bell 202 AFSK on the RF-chip tone oscillator

The existing APRS beacon application uses the level-13 interface. Physical
APRS transmission remains an open validation item.

## 8-tone proof of scheduler

The optional c/k6/mod/tx8probe.c experiment is inert by default.
Its 64 test symbols use 8 nominal audio frequencies from 450 Hz to 2550 Hz
in 300 Hz increments at 600 symbols per second. It is not a PixelQSO frame.

The separately compiled TX8_BENCH_ENABLE=1 variant uses a 96 MHz timer
and bounded TX module API, checks chip ID 0x4829 and the selected bench
frequency, refuses an occupied channel, and requests amplifier off after
keying. It clears the oscillator registers and unkeys on completion. An
emulator run with simulated register-write costs produced 64 exact tone
words and a mean 1666.7 us symbol interval, with timer and radio cleanup.

These results do not establish on-air 8-FSK capability on FD6818B: the
register map, analog RF phase continuity, distortion, and power need
physical measurements. TX may radiate even with PA disabled; tx_on may
briefly enable PA before tx_pa(0). A rated dummy load and suitable
attenuated SDR receive path are prerequisites for real tests. The
software is not yet approved for unattended operation or routine
on-air use; there is no complete frame, FEC, ID, or audio codec.

## Next checks

1. Verify the tone oscillator on this FD6818B with a safe single-tone
   conducted test, then compare two-tone transitions and all 8 tones.
2. Measure phase transitions, occupied bandwidth, deviation, actual
   symbol timing, underruns and watchdog/abort behavior.
3. Extract a reusable bounded TX driver only after hardware confirmation,
   then test known 8-FSK symbols through the GP0 and PA11 RX paths.
4. Preserve the separation between PA11 speaker PWM and actual RF
   transmit modulation. No PCM-to-MICIN board path has been established.

The separate microphone research will examine the other direction:
physical microphone through the RF chip's TX-side audio DSP to PA11.
