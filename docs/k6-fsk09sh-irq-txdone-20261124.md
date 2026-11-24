# FSK09S-H — B-chip IRQ TX_DONE gated completion diagnostic

Strict follow-up to the 2026-11-24 dummy-loaded actual #9
700-ms one-shot. The old module returned -14 on the first frame,
while the independent SDR recovered one exact 48-word payload with
public-sync correlation 0.995898. After 700ms of polling the register
telemetry showed REG02 IRQ last=0x8000, REG0C status_or=0x0220
with no REG0C[0] TX_DONE. The module's existing termination uses
only REG0C[0]. This suggests the hypothesis, not proof, that
the B chip signals TX_DONE through REG02[15].

New finite 49-word TX completion criterion: await >=220ms
from FSK start, then accept either latched REG02[15] or existing
REG0C[0]. The 49x16 payload bits plus 160 public preamble/sync
bits at 4800baud require approximately 197ms in the expected
over-air format, so a 220ms minimum interval avoids immediate
frame shortening due to stale or early IRQ. This margin is a
test assumption; the independent SDR still has the final say.
The total 1900ms hardware TX watchdog, maximum 700ms per-frame
timeout, 2x49-word fully preloaded frames, threshold24/8,
K6_MODULE_LEVEL 3, chip ID 0x4829 guard, safe PTT/power checks,
free-channel detection, FSK/PA guards and immutable saved-reg
restore are unchanged. No mid-TX FIFO refill is permitted.

## Emulator and gate evidence

The same independent compiled Cortex-M0 Thumb emulator used for
the original #9 test was pointed at the new armed source in a
separate test worktree (rather than rewriting its own model).
Three actual binary targeted tests passed:
1. Model REG0C=0, REG02[15] appears after 213ms:
   two exact 49-word frames, return98, all regs restored, TX OFF.
2. Model both TX DONE indications absent: -14, one frame, same
   fail-closed restoration and no auto-retry.
3. Deliberately early spurious REG02[15] from 20ms:
   TX exits only after 220ms, two frames, PA OFF/restored.
All previous harness tests and frozen source checks must pass too.

This is not a production firmware change or RF qualification.
Prior manifest digests 25680214... and 15f9ba1d...
are permanently exhausted. Any new actual RF attempt needs
a separate immutable SHA-checked source and new session manifest,
independent read-only Muse validation, fresh owner GitHub queue
ACK, single-use dummy-load grant, whole-bench and serial locks,
private SDR recording and complete 31-slot/slot30/VFO restoration.
Physical PASS requires independent two-frame exact RF and bounded
IRQ timing; a REG02 bit alone is not a PASS.
