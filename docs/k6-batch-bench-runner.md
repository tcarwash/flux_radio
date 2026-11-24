# Flux K6 multi-experiment bench runner

Status: **OFFLINE implementation and tests only**. The worktree is
/storage5/dev/flux-batch-bench-runner on branch
research/batch-bench-runner-20261123. No radio, SDR, serial, SPI or RF
access was performed.

The implementation is tools/k6_experiment_batch.py. It accepts a
**locally reviewed, operator-approved JSON manifest** for several
experiments run **sequentially**. It does not take automatic action
on GitHub issues and does not issue hardware leases.

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
