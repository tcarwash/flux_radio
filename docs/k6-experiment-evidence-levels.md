# Flux: what makes an experiment valid?

The current runner bundles *measurement validity*, *hardware safety*,
*independent analysis*, and *feature readiness* into a single strict
PASS/BLOCKED pathway. This makes real conducted RF observations appear
"invalid" when the downstream decoder or synchronization fails. It also
causes overly long candidate repair loops. The distinction below is a
recommended interpretation/policy update. **This document alone does not
change radio safety gates or claim that the present executor enforces
these levels.**

## Recommended result classes

**OFFLINE VERIFIED** (software fixture/emulator): include revision or
source SHA, command/fixture used, pass/fail, measured outputs and useful
negative controls. Publish to the shared SQLite results ledger. Never
file a GitHub hardware issue merely to hold software-only logs, and never
infer that model behavior was observed in actual RF.

**PHYSICAL OBSERVED** (exploratory bench measurement): a real
dummy-loaded radio run with a dated bench receipt, tuned frequency,
firmware build, test module/version, TX duration/mode, raw SDR evidence
or register trace, exact measured effect, and an independent confirmation
that the transmitter stopped and the prior radio/slot state was restored.
This is a valid measurement even if the hypothesis is false, a decoder
does not synchronize, or the modulation is not yet standards-compliant.
State which observations are **measured** versus merely inferred.
If there was no readable SDR capture or trusted hardware observation,
record an **INCONCLUSIVE/INFRA BLOCKED** attempt, not a physical PASS.

**RF QUALIFIED** (claim that a particular modulation/data receiver actually
works): all PHYSICAL OBSERVED fields plus predeclared scoring criteria
fitting the claim (e.g. known symbols, independent IQ/SDR sync and
recovery, bit error rate or equivalent frequency/deviation measurements,
negative control, repeatability if appropriate, and any spectrum/level
requirements). Do not require BER for a hardware actuator audit whose
question is only 'does this register visibly change RF?' Conversely,
never assert a working 4-FSK modem based only on synthetic discrimination.

**FEATURE READY** (safe reusable firmware capability): RF qualification
if it transmits, plus integration tests, bounded adverse conditions,
compatibility checks and appropriate regression coverage. The module's
feature-level claim is broader than any single physical experiment.

An experiment can be **COMPLETED NEGATIVE** if an appropriate physical
measurement falsifies its hypothesis. It should not be grouped with
**HARNESS FAILED**, **BLOCKED**, **INCONCLUSIVE** or a missed capture.
The three-attempt cap applies to *physical session starts* regardless
of outcome, but exhaustion alone is not scientific falsification.

## What we may be over-requiring today

1. A complete, independently reviewed SDR modem decoder is needed for
   full RF modem claims, **not** for every preliminary register/actuator
   characterization. Save raw IQ, report actual measured shifts, and
   explicitly mark limits.
2. A completely new delegated firmware-and-harness write/review cycle
   for each variant is unnecessary when an existing verified harness
   can change a bounded, predeclared parameter table with a new
   immutable receipt. Review novel code changes, not repeated data.
3. Multiple full packed-library hash comparisons can be redundant
   within one supervised session. A single before/after baseline plus
   explicit TX-off and VFO/temporary-slot restoration may be sufficient
   for a dedicated bench when no intermediate phase can continue
   after a failed restore. Keep a strict final postflight.
4. Requiring all experimental stages to return a single boolean PASS
   conflates the scientific result with whether the harness ran. Save
   observations independently of decoder qualification and note
   confidence, limitations and data provenance.
5. A single device-free "independent approval" for each already-pinned
   trivial parameter revision may add latency without new evidence.
   Review the source/pin change only if a hazardous capability or
   firmware path changed. Keep the exclusive lock and TX cutoff.

## Safety and reproducibility that should remain

- Exclusive K6/SDR hardware ownership: a single canonical bench lock,
  no overlapping agents or scheduler/manual controller.
- Dummy load and appropriate TX frequency, power, duration, positive
  TX watchdog/cutoff, and dekey-on-exit even when failures occur.
- A trusted immutable revision or parameter receipt identifying what ran.
- Reproducible raw data and instrument settings for the actual hypothesis.
- Verified TX-off, stable VFO and restored modified registers/modules.
- The agent must say whether the signal was synthetic, received off-air,
  or conducted from the actual B-chip radio.
- Never turn an inconclusive test into a false positive or use feedback
  from a failed test to silently move its pass threshold.

## Concrete current examples (November 24, 2026)

- #21 RX-only fast-SPI: 10,000 on-radio readbacks with zero errors and
  independently verified restoration, so the stated SPI primitive is
  physically verified; that does not qualify RRC-shaped 4-FSK.
- #26 REG40/native-FSK: two known 33-word frames were sent and the
  device restored, but SDR sync evidence was incomplete. This is an
  observed finite RF action with *inconclusive REG40 hypothesis*, not
  a successful decoded 4-FSK test.
- #20 REG51 signed DAC: the module returned -13 then -15 instead of
  its success marker. The source's -13 corresponds to a nonzero
  initial REG51 snapshot, and -15 to failed restore verification.
  No signed DAC RF qualification was achieved; record hardware
  blockers, not scientific disproval.

**Next implementation option:** add typed results
OFFLINE_VERIFIED/PHYSICAL_OBSERVED/RF_QUALIFIED/
COMPLETED_NEGATIVE/INCONCLUSIVE/HARNESS_FAILED. Preserve the current
supervised executor's safe postflight, but allow a bounded bench run
to publish "observed but not modem-qualified" with the evidence.
This would reduce queue churn without relaxing actual TX safety.
