# KISS TNC HIL runbook (`tools/k6_kiss_hil.py`)

One-shot, opt-in, NO-RF hardware qualification for the Flux Data-pack KISS
TNC (`c/k6/mod/fluxtnc.c`, API level 14). Code/test work only by default;
nothing here touches the radio unless `--execute` is passed with all leases.

## Preconditions (operator verifies by sight)

- K6 at known state, re-attested by the tool itself before any mutation:
  build `339e08a2b77c7679`, module-loader API 29, packed directory with 31
  occupied slots and slot 30 vacant, standby idle, no PTT, VFO A 433.500 MHz
  / VFO B 144.390 MHz, dummy load attached.
- No other agent holds the bench (`~/.cache/flux/bench.lock`) or the cable
  (`/dev/ttyUSB0`). The harness fails closed if either is busy; never disturb
  another lease.
- Bench HOLD file absent (`~/.cache/flux/bench.hold`). If present, inspect
  first; remove only after inspection.

## Steps

1. Freeze (safe anywhere):
   `python3 tools/k6_kiss_hil.py [--freeze-out FILE]`
   Review `session_digest`, `code_sha256`, entry, and level.
2. Coordinate on GitHub issue #11 out of band. A trusted actor attests the
   exact `session_digest`; save their ACK JSON (`issue`, `actor`,
   `session_digest`, `issued_at`, `expires_at`) to a file. The tool never
   fetches or posts anything.
3. Execute once, operator present:
   `python3 tools/k6_kiss_hil.py --execute --lease-ack ACK.json
   --trusted-actor NAME [--private-dir DIR]`
4. Read the private dir: `freeze.json`, `snapshot.json` (pre-install slot
   hashes), `transcript.jsonl`, `evidence.json` (TX proof).

## What the session does

Holds the same bench lock through run, uninstall and post-verify; serial lock is reacquired between cable protocol phases. Cable re-attestation → pre-install snapshot → cable
install of a signed cable-only module into verified-empty slot 30 with
byte-exact readback → live RUN (`allow_higher_api=True`) → KISS CONTROL only
(LIST / GET / SET APRS-1200 / STATUS / STOP; never type-0 DATA, no PTT/TX) →
STOP restores UART → readback-gated uninstall of exactly slot 30 → read-only
post-checks (31 original slot hashes, VFOs, build, idle) + evidence.

## Failure handling

- Pre-mutation abort (bad attestation, stale lease, busy lock): exit 2,
  nothing changed, no HOLD.
- Post-install failure (lost launch ACK, STOP refused, readback mismatch,
  erase unverified): exit 3, bench left on HOLD (`bench.hold` + transcript).
  Inspect the radio (EXIT key or a fresh STOP may be needed to restore the
  UART), compare slot 30 against the recorded `envelope_sha256`, and never
  blindly retry or erase another slot. If the temp slot identity is unknown,
  operator inspection is required before anything else.
- The tool never auto-restarts, never retries a commit, never flashes MCU
  firmware, and never transmits.
