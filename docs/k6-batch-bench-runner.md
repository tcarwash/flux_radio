# Flux K6 multi-experiment bench runner

Status: **OFFLINE implementation and tests only**. The worktree is
/storage5/dev/flux-batch-bench-runner on branch
research/batch-bench-runner-20261123. No radio, SDR, serial, SPI or RF
access was performed.

The implementation is tools/k6_experiment_batch.py. It accepts a
**locally reviewed, operator-approved JSON manifest** for several
experiments run **sequentially**. It does not take automatic action
on GitHub issues and does not issue hardware leases.

## Editable API target

The development-host API target is in `config/k6-experiment-runner.json`
(`target_module_api`, initially **29**). Change that file when the bench
firmware API advances; do not hardcode 29 into individual runner scripts.
Each job manifest may declare `minimum_module_api` as an integer. The
GitHub-intake path and any new manifests **must** populate it. A job needing
a newer API than the configured target is blocked, but an older minimum API
remains compatible (subject to host capability checks). Missing minimum
API values are reported as execution blockers, not silently assumed to be
compatible.

The CLI's dry run prints the configured API, per-job minimum API, blockers,
and an `approval_sha256` that covers both the manifest and configuration.
Changing either requires fresh approval. The editable target is **not**
proof of the API actually installed on the radio. Each runnable hardware
preflight must verify the device's live API/capabilities and fail closed on
mismatch; it must not assume API29 solely because the JSON file says 29.
The original coordinator still requires manual manifests and an ACK; GitHub
issue ingestion and shared radio/SDR resource locking are separate pending
integration tasks.

## The execution contract

1. An experiment owner submits to the shared queue issue #11 an
   emulator-qualified frozen source/compiled hash, independent SDR
   acceptance criteria, and four runnable bench harness actions:
   preflight, run, restore, verify. Submitters do not get hardware access.
2. The bench operator chooses an order consistent with current priority
   and any other active RF leases, then builds one JSON session manifest.
   There must also be session_preflight, session_restore and session_verify
   harnesses. Every action is an argv array, never a shell command; all
   actions have a hard timeout of 1–300 seconds. Source and runner scripts
   are pinned by SHA256. Worktree/capture destinations are explicit.
3. Dry-run prints the exact manifest SHA256 and sequence, executing NO
   commands. Only after independent GitHub hardware/SDR ACK, operator
   approval, and physical dummy-load confirmation may the operator
   provide --execute, --approve-digest, --ack-url, --operator,
   --dummy-load-confirmed. An expired manifest refuses to start.
4. A session lock prevents another *batch runner* from sharing that
   radio. Each child retains the canonical per-port
   serial_lock('/dev/ttyUSB0'). The parent does NOT hold this serial
   lock across children because those harnesses need to acquire it.
   **This means the session lock does not stop unrelated/uncooperative
   radio programs.** Coordination via queue ACK and child serial locks
   remains mandatory.
5. Once session preflight passes, each job is checked for source drift
   and lease expiry, preflighted, run, restored and verified. Restore
   and verify are **always attempted** even when the job fails, times out,
   or the lease expires. A failed or unverified job stops the batch,
   without proceeding to the next one. Process groups are terminated
   on timeout; TX capability must ALSO have a chip/firmware watchdog.
6. On any outcome the session-level restore and verify are attempted.
   PASS requires success for every experiment, restore, and verification.
   The operator releases the queue claim only when independently verified
   PA-off, RX mode, unchanged VFOs/registers and trusted signed module.
   The runner cannot independently enforce those physical conditions.
7. Results and command logs are stored privately under
   ~/flux-bench/batches/<session_id>/ (directory mode 0700, files 0600).
   Session IDs are non-reusable so failed run records are not overwritten.
   Share only hashes and privacy-safe measurements to GitHub.

**Limitations:** The runner validates the syntax of an ACK permalink,
not its authenticity or currency. It cannot detect an unplugged dummy
load, invalid SDR measurements, malicious harness code, or a module that
fails to restore without independent verification. Every physical
harness must be reviewed separately. This runner does not use
tools/k6_bench.py, the separate IC-7100/hamlet bench manager.

## Manifest schema (illustrative, not runnable until prepared)

Below is a one-job example; add other jobs under "jobs" to make a batch,
up to 20. All paths must be absolute. Replace the all-zero SHA values
with the actual SHA256 of each pinned source file before approval.
The commands and expiry here are placeholders; DO NOT run them.

~~~json
{
  "version": 1,
  "session_id": "k6-operator-session-01",
  "port": "/dev/ttyUSB0",
  "queue_issue_url": "https://github.com/tcarwash/flux_radio/issues/11",
  "lease_expires_utc": "2026-11-23T18:00:00-08:00",
  "session_pins": [{"path": "/ABSOLUTE/operator_bench.py", "sha256": "0000000000000000000000000000000000000000000000000000000000000000"}],
  "session_preflight": {"argv": ["/usr/bin/python3", "/ABSOLUTE/operator_bench.py", "preflight"], "timeout_seconds": 30},
  "session_restore": {"argv": ["/usr/bin/python3", "/ABSOLUTE/operator_bench.py", "restore"], "timeout_seconds": 30},
  "session_verify": {"argv": ["/usr/bin/python3", "/ABSOLUTE/operator_bench.py", "verify"], "timeout_seconds": 30},
  "jobs": [
    {
      "id": "whisper-01",
      "issue_url": "https://github.com/tcarwash/flux_radio/issues/29",
      "minimum_module_api": 29,
      "cwd": "/storage5/dev/flux-whisper8-mfsk-offline",
      "pins": [{"path": "/ABSOLUTE/whisper_bench.py", "sha256": "0000000000000000000000000000000000000000000000000000000000000000"}],
      "preflight": {"argv": ["/usr/bin/python3", "/ABSOLUTE/whisper_bench.py", "preflight"], "timeout_seconds": 30},
      "run": {"argv": ["/usr/bin/python3", "/ABSOLUTE/whisper_bench.py", "once"], "timeout_seconds": 20},
      "restore": {"argv": ["/usr/bin/python3", "/ABSOLUTE/whisper_bench.py", "restore"], "timeout_seconds": 30},
      "verify": {"argv": ["/usr/bin/python3", "/ABSOLUTE/whisper_bench.py", "score-and-verify"], "timeout_seconds": 60}
    }
  ]
}
~~~

## Commands

~~~bash
# Run the complete offline test suite (the fake device is never opened):
python3 -m pytest -q tests/test_k6_experiment_batch.py

# Preview a frozen manifest; this never runs any command:
python3 tools/k6_experiment_batch.py /path/to/session.json

# Only with independent current hardware lease ACK and approved manifest:
python3 tools/k6_experiment_batch.py /path/to/session.json \
  --execute --approve-digest <SHA256_FROM_PREVIEW> \
  --ack-url 'https://github.com/tcarwash/flux_radio/issues/11#issuecomment-<ACK_ID>' \
  --operator AG7SU --dummy-load-confirmed
~~~

Each harness's "verify" must run the independent SDR scoring and radio
restoration checks; a zero exit status from a transmitter alone does NOT
prove a passed RF experiment. No blind retries.

## Initial candidate queue

The existing ranked order remains P0 FIFO (#9), P1 Sotto DV (#10),
P2 PA11 mic (#12). Whisper8 (#29) is an **unranked proposed test**.
Mic cable removal/reconnection is a physical operator action and cannot
be unattended. The runner does not mean these jobs are all hardware
ready now. Each individual proposed job needs a frozen executable
harness, independent SDR criteria, and explicit safe restoration.
No physical batch has been claimed or run by this implementation.

## GitHub intake preview

Run `python tools/k6_experiment_queue.py` to discover open experiments from
the configured repository and coordination issue. The local
`config/k6-experiment-queue.json` holds the trusted issue author and reviewed
experiment registry. Registry entries may record `validated_module_api`;
a target change requires requalification. The preview is read-only, does not
interpret issue text as commands, and does not establish a bench claim.
It does not yet generate executable manifests or dispatch experiments.

## Trusted queue dispatch and hardware handoff (2026-11-24)

GitHub issue descriptions and comments are DATA ONLY, never an executable
command source. Only open, ranked issues authored by tcarwash qualify; all
commands, paths and hashes must come from locally reviewed files.

### OFFLINE (target-emulator) dispatcher

Local allowlist: config/k6-experiment-queue.json.
Read-only preview: python3 tools/k6_experiment_dispatch.py --all
Run one: python3 tools/k6_experiment_dispatch.py --issue 9 --run-offline
Run all registered in ranked order:
  python3 tools/k6_experiment_dispatch.py --all --run-offline
Add --publish to post a sanitized result only on a freshly reverified issue.

The dispatcher verifies the frozen worktree HEAD, clean tree, exact SHA256
pins of reviewed tests/source/entrypoint; no shell evaluation. Execution
has timeout and process-group kill, checks for source drift afterward,
and stores private evidence with mode 0600 under ~/flux-bench/offline-queue.
The K6_EXPERIMENT_OFFLINE environment flag is advisory, not a security sandbox:
register ONLY manually reviewed offline harnesses.

Initial registrations: #9 uses the original Cortex-M0 Unicorn firmware
module simulator, 81 tests; #10 uses the FDV1/Sotto C-target-emulator,
session and playout suite, 109 tests. Both passed via the actual dispatcher,
and sanitized results were posted to the issues:
#9 https://github.com/tcarwash/flux_radio/issues/9#issuecomment-7136807068
#10 https://github.com/tcarwash/flux_radio/issues/10#issuecomment-7136852803

Remaining issues require individual harness review and enrollment. Issue
body text cannot self-enroll.

### Hardware runner (not currently enrolled for live execution)

Separate allowlist: config/k6-experiment-hardware.json. Hardware manifests
are intentionally EMPTY until the first up-to-date manifest, exact radio
build and verified restoration path are reviewed.

A locally approved hardware manifest registration includes the absolute
manifest file path plus its SHA256; it may not be supplied by an issue.
Preview command: python3 tools/k6_experiment_hardware.py --issue 9
(The current preview fails closed because #9 is not hardware-enrolled.)

Future execution requires --execute, --approve-digest matching a local dry
run, --ack-url, --operator, and --dummy-load-confirmed. The live ACK comment,
posted on trusted issue #11 by tcarwash, MUST include this exact line after
substituting values:

    BENCH ACK | issue=9 | session=SESSION_ID | approval_sha256=DIGEST | operator=AG7SU | scope=whole-bench

The handoff fetches that comment live, verifies issue/author, freshness
(within 45 min), exact digest and session binding, and paginates all #11
coordination comments to refuse conflicting claims or an ended lease.
A merely syntactically valid comment link is not sufficient.

The handoff holds a cooperative whole-bench flock at
~/.cache/flux/bench.lock through the existing reviewed batch engine;
child hardware harnesses still MUST acquire the canonical nonblocking
serial_lock('/dev/ttyUSB0'), and independently enforce firmware/chip and
VFO preflight, TX watchdog, SDR evidence, restoration and verification.
A file lock cannot exclude other agents that have not adopted that same
lock; GitHub claims alone are NOT atomic reservations.

Compatibility BLOCKER: editable target API is 29 but the last reported
live radio API on issue #11 was 25, and #9's frozen physical harness
expects a different firmware build. Do not silently retarget or invoke
physical #9 until up-to-date on-radio attestation and revised review.
The passing hardware handoff tests use fake devices only, no real RF.

Regression command:
  python3 -m pytest -q tests/test_k6_experiment_queue.py tests/test_k6_experiment_dispatch.py tests/test_k6_experiment_hardware.py tests/test_k6_experiment_batch.py

## Autonomous coordinator + delegated validation (2026-11-24)

The user-level scheduler is installed by the local k6_experiment_service.py
installer and runs every ten minutes using a systemd user timer. The systemd
user manager on this workstation has lingering enabled and a real one-shot
service run returned success.

Two trusted, non-hardware jobs run automatically:

1. k6_experiment_autopilot.py --once --publish reads only trusted tcarwash
   issues ranked by issue #11 and runs locally pinned, reviewed OFFLINE
   harnesses. Private journal:
   ~/.local/state/flux-experiment-autopilot/state.json.
   One attempt per exact registration digest, journaled BEFORE execution;
   failure or crash halts further queue execution. Earlier completed tests
   are imported from valid local evidence rather than repeated.
2. k6_experiment_review.py --once discovers locally registered hardware
   manifests, submits a read-only Muse review, and records its independent
   verdict. Agent opinions are advisory vetoes, never authorization to
   execute unregistered commands.

Command: python3 tools/k6_experiment_service.py status
Disable: python3 tools/k6_experiment_service.py remove
Install: python3 tools/k6_experiment_service.py install

Direct --execute on k6_experiment_batch.py is intentionally disabled.
Only the outer k6_experiment_hardware.py wrapper may invoke its internal
batch runner after issue author, manifest/hash, ACK/lease, bench, capability
and restoration checks. Invocation entrypoints themselves must be pinned.

The nonmutating read-only physical probe
tools/k6_experiment_bench_probe.py uses canonical serial and whole-bench
flocks. Live evidence on November 24 around 08:06 PST reported K6
build 339e08a2b77c7679, API29, standby idle, FM-wide VFO A433.500MHz
and B144.390MHz; a second check agreed. No TX, SDR device, flash, SPI
or module slot access was performed.

Hardware TX is **NOT** yet automatically enabled. Local hardware
manifest registry is deliberately empty. Old FSK-09S-H #9 RF harness
expects a different radio build, VFO A and slot30 layout. An independent
read-only Muse audit recommended a new packed-safe harness; this is being
developed in an isolated API29 worktree and must pass emulator/source
qualification and physical lease gates before enrollment.

Whole-bench flock excludes only agents that honor it; GitHub claim comments
are not atomic reservations. Other hardware agents must adopt the same
whole-bench reservation. No unattended hardware retry after unknown state.

## Fully autonomous, locally approved hardware one-shots — dummy-load policy

The installed user timer now executes, in order, autonomous OFFLINE dispatch,
read-only delegated hardware review, and the guarded physical one-shot
controller (tools/k6_experiment_hardware_autopilot.py --once).

The controller does **not** interpret GitHub issue bodies as commands.
It considers only open ranked issues authored by tcarwash with an immutable,
locally registered manifest and a specific user-authorized local policy entry
in config/k6-experiment-hardware-standing.json. Policy requires:
exact manifest SHA256, expected firmware build and exact API, an expiration,
named operator AG7SU, a currently confirmed dummy-load setup and one_shot=true.
It is NOT a blanket permit for other RF configurations.

Before any operation, the controller checks the full SHA-pinned manifest,
local source/entrypoint checks, a completed independent read-only delegated
VALIDATION: APPROVED bound to the manifest+API digest, current trusted
GitHub priority/owner, GitHub login, expiry, and absence of active claims.
It then takes the cooperative whole-bench flock and repeats the checks,
durably journals an attempt BEFORE a lease or possible TX, posts a fresh
exact-session BENCH ACK from the operator account, verifies it on GitHub,
then calls the batch engine with independent session and job preflight.
Per-device canonical serial_lock remains required in each physical harness.
On verified PASS it posts PASS / RELEASE and a sanitized per-issue result;
on unexpected results it posts BLOCKED / HOLD, requiring inspection.
A power failure, process timeout or incomplete restoration NEVER
causes an automatic retry. Recovery of an unknown bench state is manual.

The session's read-only physical probe now supports the arguments
--exact-api 29 --include-slots --output private-baseline.json for a
full SHA256 snapshot of every existing packed slot before a session;
--exact-api 29 --include-slots --baseline private-baseline.json
verifies the exact existing library and VFO configuration afterward.
This complements job-specific restore/verify and catches packed library
drift outside the temporary slot.

For this non-radiating dummy-load-only lab setup, the user explicitly
exempted the FSK test fixture from a Morse identifier. This policy
does NOT grant permission for an antenna-connected RF test and does not
relax the hardware's frequency, channel-occupancy, TX-watchdog, SDR,
low-power, memory preservation or restoration gates. A dummy load does
not provide calibrated proof of zero leakage; other configurations need
their own technical/legal review.

The first hardware manifest may remain enrolled but not execute until
its exact, newly pinned revision receives independent APPROVED validation.

### First physical one-shot evidence and corrected launch (Nov 24, 09:22 PST)

Session fsk09sh-dummy-api29-20261124T171624Z was autonomously claimed,
run once and ended BLOCKED. Session and job preflight passed, packed
slot30 was installed/read back and later erased; the live module launch
was explicitly rejected by firmware with INVALID (2): no confirmed
module execution, and the SDR independent scorer found 0 valid frames.
Job restore/verify AND session restore/verify passed. A separate read-only
operator inspection under both locks then confirmed all 31 original
packed slot hashes, the same VFOs, exact firmware build/API, idle
status and empty slot30, after which a BLOCKED / RELEASE comment
released the hold. Original physical run journal retains
BLOCKED_NO_AUTO_RETRY forever for that manifest digest.

Root cause is established from firmware/k6-aura/apply-live-serial.py:
live_module_allowed accepts module level 3+ only with the explicit
live RUN 0x100 higher-permission bit, even if the radio advertises
module API29. This does NOT bypass the exact firmware/chip/source,
frequency, user-storage or RF watchdog gates. The corrected, separately
compiled and pinned host harness sets allow_higher_api=True;
a mocked-launch regression test requires that flag. Module bytes
remain frozen (SHA a14bf5a7...). The corrected session has a NEW
manifest hash, six-hour lease, specific local standing approval and
MUST receive fresh independent delegated validation before any RF.

## Physical #9 one-shot evidence (2026-11-24, dummy-load-only)

The first hardware manifest 86ab5f41... made one bounded physical attempt
on build 339e08a2b77c7679 API29. Its firmware RUN request returned INVALID
because the harness did not set the level-3 module's required live serial
higher-API 0x100 bit. No module execution was confirmed and zero RF frames
were decoded. Slot30 was removed, full original 31 packed-slot hash/VFO
restoration verified; the session's exact manifest remains permanently
BLOCKED_NO_AUTO_RETRY. The separately pinned correction is commit f57c7695.

The *new*, independently reviewed manifest 25680214... passed session
preflight, installed/read back a 1666-byte level3 module in free slot30,
and successfully invoked it with the corrected live RUN bit. The real
module returned **-14**, its internal FSK_FRAME_TIMEOUT_MS (450ms), reporting
"FSK09S H aborted. RX restored." The SDR independent public-sync detector
recovered **one** exact 48-word payload at 4800baud with correlation
0.994103 and zero sync bit errors, but not the required two frames or
validated AE IRQ timing. This is a physical RF experimental result, NOT
a successful B-chip FIFO refill or 2-frame qualifier.

The harness removed slot30 and verified original packed hashes and VFOs.
An external remote command's 90-second deadline interrupted the full
batch's remaining read-only verification stages. This was treated as
a BLOCKED/NO_AUTO_RETRY exception, NOT as a pass. Under a NEW independent
read-only inspection with both bench and canonical serial locks, all 31
original slot hashes were confirmed **byte-for-byte identical** against the
saved session-before.json, slot30 free, VFO A433.500/B144.390 MHz unchanged,
radio idle, not transmitting, original build/API unchanged. An explicit
BLOCKED / RELEASE was posted to GitHub coordination issue #11 only after
that evidence. Session #9 results and next OFFLINE investigations are posted
to experiment issue #9.

Note: A remote interactive session timeout is *not* a license to retry an
RF operation. When a parent process is interrupted, preserve the journal
in BLOCKED_NO_AUTO_RETRY, inspect hardware read-only and release only after
positive restoration evidence. For unattended timer operation the
systemd service timeout is 650 seconds, rather than the short interactive
remote shell command deadline. Firmware behavior, not runner software, is
the remaining #9 experimental blocker.

### Automatic exception/postmortem reviews

The periodic systemd user service now has FOUR steps:

1. Reviewed, SHA-pinned OFFLINE experiment executor (once-per-registration)
2. Independent read-only hardware manifest validation (Muse)
3. Independent read-only hardware postmortems for completed/blocked sessions
4. Guarded hardware executor, one exact locally-approved attempt per manifest

The postmortem stage (tools/k6_experiment_postmortem.py --once) reads only
the exact SHA-verified copy of each trusted local manifest and fixed-metric
evidence inside ~/flux-bench/batches; it excludes arbitrary radio log text
and cannot invoke radio/SDR, transmit or flash. It delegates read-only
engineering analysis through the locally installed Muse queue and retains
an owner-private durable journal in
~/.local/state/flux-experiment-autopilot/postmortem.json.
Interrupted *read-only* reviewers may be retried at most three times after
verifying the same original prompt; hardware attempts are NEVER retried.
Two independent read-only postmortems for #9 are complete:
Muse jobs 5f125d9839df (first exact-frame/timeout run) and ceb3d3e82215
(first live RUN INVALID run). Both can be inspected via muse-jobs result ID.
These reviews produce next OFFLINE/EMULATOR proposals, not hardware leases.

The complete four-stage unit was exercised under systemd --user with
Result=success, ExecMainStatus=0 while both registered hardware attempts
were marked BLOCKED_NO_AUTO_RETRY; no repeat TX occurred. The timer
remains enabled to process additional locally approved experiments and
review results in order.
