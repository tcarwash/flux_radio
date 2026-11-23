# Flux native-FSK FIFO — offline IRQ interpretation and timing audit

**Date:** 2026-11-23
**Branch/worktree:** `research/offline-fsk-irq-lab-20261123` / `/storage5/dev/flux-fsk-offline-irq-lab`
**Shared queue:** [#7 IRQ audit](https://github.com/tcarwash/flux_radio/issues/7) and [#8 FIFO model](https://github.com/tcarwash/flux_radio/issues/8)
**Scope:** Offline code audit and **hypothetical** FIFO simulation only. No radio, serial, SDR, physical TX, flash, or active test-branch modifications.

## Finding: interrupt-enable mask and reported event code differ

The older original FD6818 programming guide distinguishes:

| Purpose | Register | Form |
| --- | --- | --- |
| Enable FSK FIFO almost-empty interrupt | `REG_3F[14]` | A **bit** in an interrupt-enable mask; `0x4000` |
| FIFO almost-empty event after IRQ | `REG_02[3:0]` | An **encoded event number**: decimal 14, i.e. `0xE` |
| Set presumed FIFO TX almost-empty threshold | `REG_5E[11:6]` | Count of words, older family only |

Reference: [original FD6818 programming guide](https://www.glaver.org/blog/UV-17/FD6818.pdf), register summary / interrupt settings (approximately printed pages 50 and 56); also see original [FSK-09 notes](k6-fsk-09-fifo-boundary.md). The URL may not always be available directly; the search-indexed text and alternate mirrors are not a substitute for checking the FD6818**B** silicon revision.

Existing `c/k6/mod/fsk_refill.c` on `research/native-fsk-pdm` defines `TX_FIFO_AE 0x4000u` and tests `(irq & TX_FIFO_AE)` on the value **read from `REG_02`**. That is inconsistent with the *older FD6818* event-code representation. The original C file has **not** been changed; tests retain the original predicate as a control.

There is a second, independent blocker. The actual FSK-09 IRQ readback was consistently `REG_02 = 0x0800`. The lower nibble is `0`, not `14`; it neither passes the legacy bit-14 predicate nor the proposed event-code predicate. **Therefore changing the decoder alone would not rescue the recorded RF attempt.** We still do not know why the event was absent, or whether FD6818B shares this register behavior.

## Test vector results

| REG_02 sample | Original predicate `(reg & 0x4000) != 0` | Candidate event predicate `(reg & 0x000F) == 0x000E` |
| --- | --- | --- |
| `0x000E` | no | yes |
| `0x080E` | no | yes |
| **`0x0800` (observed)** | **no** | **no** |
| `0x4000` | yes (possible false grant) | no |
| `0x000F` (frame done code) | no | no |

These are deterministic register-decoding tests, not hardware data.

## Hypothetical finite 128-word FIFO simulation

The self-contained model is `tools/fsk_irq_offline.py`, tested by `tests/test_fsk_irq_offline.py`.

**Explicit model assumptions** (unverified on FD6818B):

- 64 × 16-bit word FIFO; 4,800 payload bits/s; 128-word declared hardware frame.
- Initially load 48 words, after which one word is consumed each 3.333 ms; no TX preamble, SPI overhead, framing gap or physical phase is modeled.
- Threshold 24 words; speculative **latched** `REG_02` event number 14 once occupancy falls to/below threshold; events re-arm after filling above threshold.
- Refill batches of 32, 32, then 16 words, preserving the FSK-09 schedule and nominal time guards (65/170/280 ms).
- Polling at 1-ms resolution. Never refill once FIFO reaches zero or once a write would exceed physical or declared-frame capacity.

**Results:**

| Scenario | Offline result |
| --- | --- |
| Candidate event-code predicate + hypothetical event-14 IRQ | **COMPLETE** at 427 ms, 128/128 words in order; refills at **80, 187, 294 ms** |
| Original predicate + event-code IRQ | **UNDERFLOW** at 160 ms; 48 words loaded, 0 appended |
| Recorded constant `0x0800` trace + either predicate | **UNDERFLOW** at 160 ms; no refill grant |
| No IRQ at all | **UNDERFLOW** at 160 ms |
| Original predicate + hypothetical one-hot bit-14 IRQ | COMPLETE, demonstrating model dependence on encoding |
| Candidate event-code predicate with 30/60/79 ms service latency | COMPLETE *within zero-SPI-overhead simulation only* |
| Candidate event-code predicate with 80/90 ms service latency | **UNDERFLOW** at 160 ms; zero occupancy is rejected |
| Declared frame too short to fit appended data | Rejected: **FRAME_LENGTH_EXCEEDED** |
| Hypothetically insufficient FIFO capacity | Rejected: **FIFO_OVERFLOW** |

At ideal threshold crossing, 24 words correspond to 80 ms of buffered symbols. This is **not** an allowable 80-ms software budget: CPU scheduling, SPI transfer time, wakeup jitter, event persistence, preamble timing and hardware occupancy uncertainty all require margin. A modeled success at 79 ms has effectively no reserve.

Observed RF burst durations in the FSK-09 report include physical framing and can differ from the 160-ms pure-payload model for 48 words. The simulator's underflow is a **logical prediction**, not an observation of FIFO status on RF.

## Reproduce

```bash
python3 -m pytest -q -rs tests/test_fsk_irq_offline.py tests/test_k6_fsk_refill.py
python3 tools/fsk_irq_offline.py --decoder event --irq-source event
python3 tools/fsk_irq_offline.py --decoder event --irq-source observed_0800
python3 tools/fsk_irq_offline.py --decoder original --irq-source event
```

Final combined test run on 2026-11-23: **27 passed, 8 skipped**. The skipped tests are the preexisting ARM/Unicorn-based C emulator cases because `unicorn` is not installed in the active Python environment. The new pure-Python offline tests run. The simulator is not the radio and does not establish continuous operation.

## Go/no-go conclusions and next safe steps

1. **Confirmed source-level issue:** Original `REG_02` predicate is not consistent with older FD6818 event decoding. This is a candidate firmware fix, *not yet proven on FD6818B*.
2. **Critical unresolved result:** Actual `0x0800` trace contains **no encoded FIFO almost-empty event**, so further investigations must establish event generation/clearing before granting TX FIFO writes.
3. **Finite frame only:** Successful model stops at 128 declared words. No unbounded streaming, continuous FIFO refilling beyond the declared length, or RF phase continuity is proved.
4. **Next bench step is status-only:** With explicit hardware lease, sample live IRQ and status around known occupancy and verify correct `REG_02` semantics, event persistence and read/clear behavior. Do **not** use `0x0800` as a refill grant.
5. **Only after a real headroom signal is qualified:** One 16-bit FIFO append under the existing hard lease and independent SDR decode. Keep active experiments undisturbed.

The current work is limited to a separate offline test/model/document. It does not alter original `fsk_refill.c`.
