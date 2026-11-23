# FSK-09S-C/D/E/F: archived-IQ framing / symbol-clock audit

Date: 2026-11-23. **OFFLINE ONLY**. Isolated worktree
`/storage5/dev/flux-fifo-frame-audit`; scope: shared experiment #9 and
offline root-cause issue #15. No K6 serial, radio, SDR device, TX or flashing
was used. The prospective hardware PASS/FAIL verdicts remain unchanged.

## New finding

The apparent *additional* bit/symbol slips in FSK-09S-F arose from a
receiver-clock ambiguity, **not from bit errors that survive a nominal
4,800-baud re-decode**. The original public-sync search allowed symbol rates
4,790.4 / 4,800 / 4,809.6 baud and selected **4,790.4** for F, with
sync correlation **0.992610**. Fixed **4,800** gives sync correlation
**0.992576** (difference only 0.000034) but restores a *perfect*
post-hoc bit match to a one-word-shift hypothesis:

`0x00F8 || original_expected_words[0..58]`

Every bit in all **59 intended words** after the unexpected header matches
exactly, including **11 of 12** words written while TX was running. The
original strict 60-word frame still fails because the stray `0x00F8`
displaces the 60th intended word beyond the programmed frame length.

The F failure therefore has **one demonstrable framing artifact** and a
**separate, now-diagnosed offline symbol-clock estimator issue**. It has
*not* established an RF hardware symbol slip at 70 ms.

## Cross-capture forensic comparison

Re-ran the original archived `.npy` captures using **only the public
16x0x55 preamble and 076b3e36 sync** for acquisition and a fixed
4,800-baud clock. Expected data were consulted **after** recovering RF
payload bits; the `0x00F8` shift is an explicitly post-hoc hypothesis,
not part of frame synchronization or a revised success criterion.

| Experiment | Frames | Strict frame BER | Post-hoc hypothesis BER | In-flight data recovered under hypothesis |
| --- | ---: | --- | --- | --- |
| C: 49 fully preloaded, then 48+1 | 2 | first **0/784**, second **191/784** errors | second **0/784** with leading `0x00F8` | 0 of 1 (marker displaced) |
| D: standalone 48+1 | 1 | **0/784** errors | not needed | **1 of 1**, formally RF-proven |
| E: 48 + four batches of 4 | 1 | **316/1024** errors | **0/1024** after `0x00F8` insertion | **15 of 16**, post-hoc only |
| F: 48 + three batches of 4 | 1 | **281/960** errors at fixed 4800 | **0/960** after `0x00F8` insertion | **11 of 12**, post-hoc only |

The original F analysis reported **363/960 errors**, because its
unconstrained decoder selected 4790.4 baud. Both original and new values
must be retained. E's original receiver selected 4800 and the same
strict 316 errors recur. The existing recordings' SHA-256 hashes are
checked and recorded by the audit tool.

**Important:** This does not retroactively pass E/F. Only D passes its
prospective full-frame criterion. Nevertheless the E/F data now show that
multiple distinct in-flight words **reached the RF output in correct
order** in those single frames, and that the *additional* F corruption
was decoder clock drift.

## Source-level audit

The module ABI in `firmware/k6-aura/aura-pager.patch` implements
`mod_fd_write(reg,value)` by a direct synchronous call to
`app.radio.fd6818.reg_write(syst,reg,value)`, after the TX watchdog
guard. There is **no explicit FIFO software queue** at this ABI layer.

C/D/E/F all use `REG_59=0x00F8`, followed by interrupt clear, frame
length, `REG_59=0x80F8` (reset/clear), `REG_59=0x00F8`,
FIFO `REG_5F` preload, and `REG_59=0x08F8` (start).
The unexpected first RF data word is also **0x00F8**.
This numerical equality is a lead, **not a demonstrated electrical or
SPI source**. It occurs in the second frame of C and the standalone E/F,
but not the successful standalone D. The D/E/F source differs in length
and append pattern, not in the basic modem reset/preload/start sequence.

Do not assert the source is a REG_59 write: B-chip reset/FIFO behavior,
possibly stale data, control/PDU framing, and serial/SPI timing remain
hypotheses. No physical REG_5F destructive FIFO read or unvalidated
REG_5E almost-empty IRQ was used.

## Reproduction

Run from this dedicated worktree:

```sh
python3 -m pytest -q tests/test_k6_fsk09s_frame_audit.py
python3 tools/k6_fsk09s_frame_audit.py \
  --out /tmp/flux-fsk09s-frame-audit.json
```

Input is the preserved RF archive under
`/home/tyler/flux-bench/tx-injection-20261122/fsk09s-{oneword,fourbatch,threebatch}/`.
The CLI never overwrites the output file. It emits strict comparisons,
separately labeled *post-hoc* displaced-word scores, acquisition scores,
fixed/previous rate choices and raw IQ SHA-256. It opens no radios.

## Next physical discrimination (NOT run or authorized by this document)

1. **Stand-alone fully preloaded 60-word control**, exact same configuration
   and `REG_59` clear/start path as F. Keep the previously established
   64-word bound. Freeze source hash and receiver *before* RF. If
   `0x00F8` appears even with no in-flight writes, the append operation
   itself is not required for the artifact.
2. If the control is exact, test a separately precommitted **48+2** 50-word
   frame with two single-word writes on distinct bounded time marks.
   Demand exact *entire* 100-byte payload, including last two markers.
   Any unexpected header makes the run **FAIL**, even if a post-hoc
   displacement comparison is perfect.
3. Preserve public-sync-only acquisition and fixed 4800-baud sampling as
   precommitted acquisition assumptions. Also record multiple candidate
   rate/correlation scores to expose near-ties, but never optimize BER
   against known payload to declare a pass.
4. Use the established canonical **nonblocking serial lock**, explicitly
   granted hardware lease, user-approved dummy load/short watchdog, original
   slot/VFO backup and postflight restoration, single bounded test per
   authorization. Another project's lease always takes precedence.

Neither these observations nor 64-word FIFO capacity proves >64-word
continuous refill, IRQ-based headroom, indefinite streaming, or arbitrary
complex-IQ synthesis.
