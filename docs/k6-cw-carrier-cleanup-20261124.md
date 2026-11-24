# Clean CW groundwork — bounded key cancellation and PA cleanup

**2026-11-24 · Non-Sotto CW track · offline compiled firmware and mocked API only.**

This is deliberately a **small, low-regret CW safety fix**, separate from
PA envelope shaping. It improves the existing `c/k6/mod/cwkey.c` once-off
CW identification module, which already holds the PLL configured and keys
by `tx_pa(1/0)`. It does **not** change the PA power level, frequency,
`REG36`/`REG19`/`REG30` register programming, actual TX gating logic,
firmware API layout, CW audio shaping or other modems. No radio or dummy-load
experiment was performed. Physical TX envelope qualification is queued at
[CW TX-35 #38](https://github.com/tcarwash/flux_radio/issues/38) and remains
open/unclaimed. AM and polar-mode tuning also remain separate.

## Why

The source formerly made one **300-ms uninterruptible lead-in delay** after
`tx_on`; a human key-down during the delay could be missed for the entire
interval. It also performed `tx_pa(1)` **before** checking `key_down()` at
each mark, and slept for a whole Morse unit (60 ms at 20 WPM) between
cancellation checks. On a pressed key, a full mark could therefore remain
on longer than necessary. The final `tx_pa(0)` result was ignored, and
constructing a verbose module line with an oversized compile-time `CW_TEXT`
could overflow the module's 64-byte buffer.

## Change

- A **10,000-µs maximum** polling interval (`wait_keyable`) checks the
  actual API `key_down` before and after an interval, in lead-in *and*
  during Morse elements. It checks **before every PA state transition**;
  it never intentionally re-asserts PA after observing a stop request.
- `tx_on()` is followed by `tx_pa(0)` **before another `ms()` read**,
  minimizing a possible initial PA-on window. Any failed `tx_pa` operation
  returns a failure, while `tx_off()` is always attempted after successful
  `tx_on` (including stop/error paths). The host firmware's independent TX
  watchdog and band lock remain in force.
- Clear-channel waiting now measures elapsed **unsigned 32-bit time** from
  the start of the wait, preserving the limit after clock wrap. The text
  length is checked before formatting diagnostic lines into a fixed buffer.
  The `tx_on` watchdog request rounds up per-unit duration instead of
  truncating a non-integer WPM's milliseconds.

**Boundary:** This bounds *software stop-request polling latency* to about
10 ms, **not physical RF turnoff latency**. A very short key tap might still
fall between polls, the core may have higher priority work, `delay_us` and
`tx_pa` can block, and the actual RF tail is still governed by unknown PA
and silicon behavior. The K6 host core—not this module—must guarantee hard
PA-off on timeout. `tx_on()` itself may briefly assert PA; its true behavior
requires the physical sweep and separate host-core audit. Nothing here
provides clean 4–8-ms CW RF envelopes or proves reduced key clicks.

## Reproduce with no hardware

`tests/fixtures/k6_cw_carrier_mock.c` includes the *real module C source*,
using mocked API3 `tx_on/tx_pa/tx_off/ms/delay_us/key_down` callbacks. It
records state transitions and simulates `tx_on` initially asserting PA:

```sh
python -m pytest -q tests/test_k6_cw_carrier_cleanup.py
```

The test checks: nominal one-element CW ID, PA-off before the first time
read after TX entry, cancel during PLL lead-in and just before first mark,
stop during an on-air mark (software poll ≤10 ms), successful rollback
after failed `tx_pa(1)` or `tx_pa(0)`, TX-on refusal, busy-channel timeout,
32-bit module-clock rollover and oversized compile-time CW text rejection.
The harness neither opens a serial device nor sends RF. The same source
also compiles with Clang for `armv6m-none-eabi` Cortex-M0, no heap required.

At `-Os` the standalone ARM object `.text` changed from **2,483 to
2,623 bytes** (+140 B); the full firmware image/layout and exact linked
stack use are **not** established by that object measurement. These
changes do not require the extra SPI borrow or PCM buffers used by Sotto.

## Independent reviews

- **Antigravity Delegate**, independent read-only review (job
  `478d6845fdbe`, `gemini-4-hydrogen-medium`): called out lead-in delay,
  PA-on-before-stop, missing final cleanup error, elapsed-time wrap handling,
  and diagnostics length bounds. Its host-API behavior analysis was used as
  *review evidence*; all modifications and tests were performed separately
  by the coordinating agent. Other higher-scope suggestions such as precise
  Morse deadline pacing and host `tx_on()` PA startup are **not** solved.
- **Muse Delegate**, read-only modulation alternatives (job
  `ded081917951`): recommended preserving PLL hold, testing physical
  hard-gate envelope emissions independently from any receive-side FIR,
  and keeping Bessel-null carrier redistribution a **different, deferred**
  experiment. Bessel-null shifts RF energy to sidebands and does *not*
  physically switch RF off; it is not an immediate CW cleanup substitute.

## Next low-regret tasks

1. Audit the **host-level** `tx_on()` and `tx_pa()` initial PA assertion and
   watchdog restoration in a separate narrowly-scoped worktree; avoid a
   direct RF register tweak without a firm host mask/safety proof.
2. Add a deterministic **PLL/phase discontinuity diagnostic** using captured
   or recorded IQ, distinguishing PA power chirp from analog envelope clicks.
3. Wait for the physical conducted #38 TX filter and edge timing captures
   before any adaptive `REG36` compensation table or flashable AM driver.

**Sotto development remains assigned to the other integration thread.**
