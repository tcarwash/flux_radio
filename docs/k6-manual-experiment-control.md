# Flux manual experiment control

Use the shared development host command flux-experiments to temporarily
take ownership of the *physical* experiment scheduler. It never creates a
second radio executor, and does not bypass the established hardware lock,
dummy load, finite TX cutoff, manifest/source pins, or on-radio restoration.

The physical pipeline has two scheduled timers, both paused during manual
ownership: flux-experiment-autopilot.timer (supervised hardware) and
flux-experiment-candidate.timer (candidate repair/builder). The independent
15-minute device-free fixture test timer continues running.

## A single agent takeover

    flux-experiments status
    flux-experiments takeover --owner "your-agent-or-thread"

The takeover prints a newly generated lease token once. Store it securely
in the owning agent's work context; do not put it in GitHub or a public log.
A second agent cannot take over without a release. If a physical or candidate
service is still running, the request refuses rather than interrupting it.

An owner may work with source/worktrees, submit hardware candidates using
existing flux-queue submit commands, then manually advance the trusted
pipeline. After substituting the lease token, for example:

    flux-experiments run --lease TOKEN --stage candidate
    flux-experiments run --lease TOKEN --stage promotion
    flux-experiments run --lease TOKEN --stage review
    flux-experiments run --lease TOKEN --stage grants

To execute one specifically reviewed and already authorized physical
experiment, while the scheduler is suspended:

    flux-experiments run --lease TOKEN --stage hardware --issue 29

The command refuses unapproved issues or exhausted attempts, and uses the
same reviewed controller, GitHub coordination, session pins, radio lock,
watchdog and postflight as scheduled execution. It never executes a
GitHub comment or user-supplied shell command.

The stage named cycle runs one complete supervised hardware unit through
systemd without restarting the periodic timer. Other trusted stage names
include intake, postmortem, closeout and candidate. The owning agent can
inspect the returned full log path, result and return code. Only one manual
stage can run at a time; run, takeover, and release are serialized.

    flux-experiments run --lease TOKEN --stage cycle

When done:

    flux-experiments release --lease TOKEN

The original timer-active states are restored. Manual ownership persists
if the agent crashes; the scheduler remains suspended, preventing two
uncoordinated experiment sources. This means a crashed/abandoned owner
needs deliberate host-side recovery by the owner before automation resumes.
Other agents can inspect the owner via flux-experiments status.

The agent should record each experiment's actual measurements, including
negative or inconclusive outcomes, in the shared evidence system. A failed
hardware test still consumes one of the three physical attempts.

## Distinguish result validity from RF qualification

See docs/k6-experiment-evidence-levels.md. Manual operation cannot convert
offline PASS or a basic physical observation into full RF modem qualification.
