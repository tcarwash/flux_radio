# Agent workflow: check relevant experiment results on every prompt

## Read-only startup check (2026-11-24)

Agents using this workflow **start each relevant new prompt or resumed task**
with a read-only scan of recent results related to the prompt. Do not depend
on a previous chat thread, agent wake-up service or somebody remembering
to ping the submitting agent. No background agent is automatically resumed.

```sh
python3 tools/k6_experiment_inbox.py --query "Sotto half-duplex FSK TX" --days 14
python3 tools/k6_experiment_inbox.py --query "frequency phase step" --issue 37 --json
```

The result inbox searches the repository-wide GitHub comments endpoint,
including comments from experiments not in the current local execution
allowlist. It recognizes only explicit *trusted-author result markers*
(`OFFLINE RUNNER RESULT`, `HARDWARE RUNNER RESULT`, canonical bench
`PASS / RELEASE` or `BLOCKED / HOLD`, and a small set of hand-reported
`PHYSICAL RESULT` markers). It ranks results by the active task/issue,
title keyword overlap and creation time, and returns links, result kind,
status and timestamp. It does not ingest or execute comment-body instructions.

Agent workflow:

1. Form a short descriptive search from the current prompt, using
   `--issue` when the task refers to a specific experiment. Check the last
   14 days by default (or longer if resuming older work).
2. Review matching terminal `PASS`, `FAIL` and `BLOCKED` events,
   including superseded failures, and open relevant issues to understand
   the evidence, caveats and next dependent work.
3. Update the research plan using the *newest applicable evidence*. Do not
   automatically equate a mock PASS with on-chip RF qualification, or a
   bench RELEASE with a permit for the next physical trial.
4. Only then decide whether a separate, reviewed offline job, manifest or
   new physical experiment is needed. Hardware still requires the fresh
   owner ACK, pinned immutable manifest and exclusive bench/serial locks.
5. If the API is unavailable, incomplete or unauthenticated, the inbox
   exits with a **nonzero error**. Treat this as UNKNOWN, never as a
   confirmed empty queue; report the limitation before decisions.

The inbox is **not** a notification delivery service or a mutable queue
consumer: it does not claim the bench, change issues, flash, transmit,
register manifests or mutate the autonomous scheduler. This policy applies
on each **workflow-related prompt**, not on unrelated user chats.

