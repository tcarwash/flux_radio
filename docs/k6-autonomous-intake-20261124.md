# Flux K6 autonomous queue intake and preparation

November 24, 2026. The former scheduler checked only three locally pinned
OFFLINE jobs and one consumed physical manifest. It never discovered new
open GitHub issues. This made a green timer misleading when work awaited the
bench.

## Execution pipeline — ~10 minute systemd timer

1. New read-only k6_experiment_intake.py: scan all open owner-authored
   [Flux RF][OFFLINE] / [Flux RF][HARDWARE...] issues across the repo, even
   ones unranked in #11. Trust only the account defined by the local registry.
   Prioritize the #30 Sotto session and existing #21/#26/#20/#24 research,
   then the existing canonical queue order and finally remaining issues.
   The GH issue body and the newest owner-authored result comments are DATA,
   NEVER a command or authorization. Exclude intake's own comments from
   fingerprints to prevent an endless self-trigger loop.
2. Delegate no more than one fresh read-only preparation assessment per
   cycle, six per day, and only one outstanding at a time. Muse receives
   JSON-escaped issue data and a fixed prompt prohibiting shell, radio,
   serial, SDR, external writes, GitHub writes, code execution and RF.
3. In later cycles retrieve the exact worker receipt, parse a constrained
   INTAKE gate, and post one idempotent GitHub comment to the issue:
   INTAKE ASSESSMENT | digest | NEEDS_OFFLINE / NEEDS_PINNED_HARNESS /
   READY_FOR_INDEPENDENT_REVIEW / NEEDS_OPERATOR / NEEDS_FIRMWARE /
   DEPENDENCY_BLOCKED / INSUFFICIENT_EVIDENCE. This comment is advisory,
   not RF evidence; the agents' prompt-time inbox will include it.
4. Run the existing pinned OFFLINE executor, independent hardware review,
   postmortem and physical one-shot executor as before. These are separate
   trusted-code safety stages. Intake NEVER generates a hardware manifest,
   operator grant or lease, flash action, or transmission.

Durable private journal is under
~/.local/state/flux-experiment-autopilot/intake.json with frozen prompts and
a read-only summary snapshot in that directory. On restart, submitted jobs
are resumed by receipt; ambiguous submissions are BLOCKED rather than
repeated. Editing an issue title/body changes its intake identity;
comment updates do not re-trigger a worker.

## Health and operator checks

    cd /storage5/dev/flux-batch-api-config
    python3 tools/k6_experiment_intake.py --dry-run
    python3 tools/k6_experiment_intake.py --once --max-new 0 --no-publish
    python3 tools/k6_experiment_status.py status --json
    journalctl --user -u flux-experiment-autopilot.service --since today

The status report shows how many GitHub RF experiments have not yet been
reviewed, rather than reporting success when the local executor list is empty.
The bench lease remains independently governed by queue #11 and the port
lock; an unclaimed bench is not authorization to run RF.

## Deliberate physical safeguard

This makes discovery and preparation assessment autonomous. It does not
authorize a general-purpose agent to generate and execute arbitrary firmware,
radio commands or unreviewed test scripts from GitHub. Agents must still
implement and validate clean pinned experiments in separate worktrees;
hardware then requires an independent source/manifest safety review, a
separate exact one-shot operator approval, dummy load, live bench lock,
device preflight, watchdog, independent SDR where needed, and restoration.

The next possible enhancement is isolated candidate-code preparation in
an explicitly restricted write worktree, with independent automatic tests
and a hard veto before enrolment. Such workers must not have unrestricted
shell or device access.

## Unit-file source of truth

Tracked service and timer templates are in systemd/. The installed user
units under ~/.config/systemd/user/ are updated to match these copies.
Do not remove the independent hardware gate from the service or make
a raw GitHub issue a shell command. On future installs, deploy the
tracked copies and run systemctl --user daemon-reload, then enable the timer.
