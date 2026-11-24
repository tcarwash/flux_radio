# FSK09S-H follow-up: bounded TX-completion timing diagnostic (hardware deferred)

**Hypothesis:** the B-chip's one-frame return -14 at the old 450-ms
REG_0C TX-done timeout may be due to a false/late completion observable
through IRQ/status sampling rather than missing RF. Increase the per-frame
window to **700 ms**, below the single **1900 ms firmware TX lease**:
two stalled-frame windows plus 10-ms interframe gap and setup are bounded
by the transmitter hardware watchdog.

**Do NOT reuse the earlier #9 authorization.** This is a new module and
single-use test needing a fresh frozen manifest, exact radio firmware/API
and chip ID, independent review, new owner bench ACK, dummy-load confirmation
and exclusive bench/serial locks. Existing queue #11 retains precedence.

Changes:
- TX-done timeout **450 -> 700 ms**, no change to FIFO payload, 4800-baud,
  IRQ thresholds 24/8, register schedule, 49-word preload or 1900-ms lease.
- Report only initialized IRQ/status traces **after TX OFF and restoration**,
  including a first-frame timeout. Never trust IRQ for mid-TX FIFO writes.
- Previous manifest 25680214... stays permanently nonretryable.
- New compiled module 1690 B vs old 1666 B, all transitive pins required.

## Independent offline validation

Actual compiled Cortex-M0 Thumb module executed in Unicorn using the
previous independent B-chip behavioral model, pointed to this new source.
All five test cases restored RFIC registers and ended with TX off:

| Modeled case | Return | Observation |
| --- | --- | --- |
| Normal bit14 IRQ | 98 | 2 physical frames |
| No TX-done | -14 | 1 frame, partial AE0 after de-key |
| Alternate IRQ event14 | 98 | 2 frames |
| 350-us modeled writes | 98 | 2 frames |
| Power switch interrupt | -4 | 1 frame, restored |

Model results are not physical B-chip proof. New packed-harness tests and
source/hash gates passed. Only one hardware module invocation is permitted.

**Physical PASS criteria:** two independently decoded exact 49-word public
sync SDR frames, original signed library/VFOs restored, and two complete
AE traces satisfying the predeclared timing discriminator. A first frame,
return -14, unknown restoration or missing independent IQ is FAIL/INCONCLUSIVE.
Partial AE telemetry remains diagnostic evidence, not refill proof.

This document grants NO hardware authorization.
