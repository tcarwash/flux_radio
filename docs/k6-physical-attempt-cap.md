# Flux physical experiment attempt budget (2026-11-24)

A single Flux RF issue can use at most **3 supervised physical session attempts**
across all manifest SHA values, revisions, delegated repairs and controller
restarts. These are physical *session starts*, not fixture tests, offline
compilations or rejected independent reviews. The immutable hardware journal
under ~/.local/state/flux-experiment-autopilot/hardware.json is the source of
truth. Existing successful historic experiments are preserved as PASS, not
retroactively closed as failures.

The cap is enforced at three separate controller boundaries:
- Candidate promotion will not enroll another frozen manifest for an exhausted issue.
- Permit generation will not approve another one-shot for that issue, even with new source pins.
- The supervisor checks again under its journal and bench locks before starting a session.

Device-free candidate repairs also skip exhausted physical issues. Editing
the original issue or changing manifest parameters cannot reset the count.

## Terminal failed issue closeout

After the hardware stage, the recurring service executes:

    python3 tools/k6_experiment_failed_closeout.py --once

Use --preview for read-only inspection. Closeout selects exhausted issues
with no successful hardware session and a terminal failed latest attempt
whose whole-radio session restore and verify are both PASS. It posts a
FAILED evidence comment, adds label experiment:failed, and closes the
GitHub issue with reason "not planned". GitHub does not have a failed
closure reason; the label and summary unambiguously record FAILED, not PASS.

If radio restoration is unverified, closeout holds rather than declaring
it safe or closing. The bench must be independently reconciled first.
No automatic retry is authorized.

Closed results remain searchable:

    flux-issues search --state closed --query "REG51"
    gh issue list --repo tcarwash/flux_radio --state closed --label experiment:failed

The runner status shows FAILED_CLOSED instead of repeatedly reporting
the same blocked experiment as awaiting retry.

## Revising and resubmitting

An interested agent may create a NEW hardware experiment issue referencing
the closed failure, results, specific changed parameters or harness, and why
the difference should address the failure. Merely changing a manifest SHA
on the same issue is not a new trial. Submit through the existing reviewed
trusted recipe or flux-queue submit pathway. Fresh bench authority,
source pins, and independent review remain required. Durable journal
history is never erased or reset.

New software-only experiments continue directly in device-free fixtures,
with evidence published via flux-offline-results, without GitHub issues
or physical attempt budget.

This does not relax dummy-load rules, finite TX cutoff, bench exclusivity,
SDR evidence, or original-state restoration. Full raw evidence remains
on the development host.
