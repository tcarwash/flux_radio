# PB7 flashlight notification experiments

## Physical K6 results (2026-11-23, user observations)

* Flashlight is GPIO PB7, active high. Manual full-on/off uses GPIO.
* LED DEMO initial TIM17 AF2 pad test did not establish native PWM.
* LED DEMO revision 3bfad988: LCD reported GPIO software PWM only. User confirmed
  the 20% peak quadratic breathing pattern looks good. Reference envelope:
  1 kHz carrier, 2 s rise, 2 s fall, 10 ms envelope steps.
* LED DMA revision 7085cbb5: TIM16 DMA route FAIL.
* LED DMA revision c4908cd5: user photographed the route diagnostics:

```
DMA route FAIL
T16:2--3!!4--6!!7!!
T15:2--3--4--6!!7!!
T03:2--3!!4!!6!!7!!
U/C paced ! unpaced
- none ? wrong rate
```

Each pair represents timer update and compare requests, respectively.
No tested channel met the paced-transfer acceptance criteria. '!' can mean
transfers without the intended timer pacing (or pre-existing status flags in
the probe); '-' means no observed transfers. This does not establish that all
possible remaps fail. Avoid DMA1 and DMA5, which are used by RF capture and
audio playback. The diagnostic intentionally did not change SYSCFG remapping.
No PWM was applied on this test, and there was no firmware reflash.

## Next vector: TIM16 edge interrupts

Use the confirmed software-PWM brightness curve but move edge timing out of
the main loop. A low-priority TIM16 update/compare ISR can write PB7 BSRR
directly, with no DMA, RF register access, or busy waiting. At a 1 kHz carrier
there are at most 2,000 interrupt entries/sec. Measure actual load and edge
latency rather than assume overhead. Use 0% and 100% GPIO endpoints without
continuous interrupts. Prefer a fail-dark strategy for late compare/update
service, and disable PWM during critical RX/TX or when timers are unavailable.

TIM16 is not identified as used in the audited main and Sotto working trees.
Do not take TIM1 (audio/CW), TIM6 (AFSK/SSTV), TIM14 (uptime/TX watchdog), or
TIM17 (RF capture).

Important: Cortex-M0 vector routing in this firmware is not ordinary VTOR
relocation. The TX watchdog installs an SRAM vector table, maps it only
while armed, and restores boot mapping and flash prefetch at rest. All
SYSCFG CFGR1 writes use an alignment/delay workaround for a previously
observed instruction-fetch fault. Integrate any new interrupt via that
existing owner, not an independent module remap. Preserve TIM14 safety
semantics and bootloader vectors. Audit the current branch/profile's vector
implementation before patching. Prototype with an opt-in build, emulator
tests for vector ownership and cleanup, and physical checks for jitter,
receiver noise, and coexistence with Sotto/IQ. Manual flashlight overrides
notifications; release TIM16 and return to original GPIO state when idle.
