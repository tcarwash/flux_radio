# Autonomous Flux bench: candidate authoring through one-shot radio tests

Updated 2026-11-24. The installed user service `flux-experiment-autopilot.service` owns the radio-test workflow on the developer bench; the K6 is connected to a dummy load. The owner AG7SU authorized finite RF transmit experiments, under the safety controls below. Every ten minutes the timer launches one **bounded** controller cycle.

## Eight-stage pipeline

1. GitHub intake: discover owner-authored `[Flux RF]` issues, gather trusted author’s prior results as untrusted *data*, cap read-only preparation assessments, publish durable issue-level gates.
2. Candidate builder: only explicitly approved local recipes in `config/k6-experiment-builder.json`, never arbitrary GitHub executable instructions. A separate detached Git worktree is created from an exact local remote ref; a Muse delegate can edit **only** the listed files. It runs in bubblewrap with `/dev` replaced by a fresh empty device tree and the shell tool disabled. The delegate cannot operate the K6, USB/serial port, or SDR. Bounded time and daily budgets; ambiguous jobs never auto-retry.
3. Candidate promotion: supervisor reruns allowlisted tests in a no-network, device-free sandbox, verifies source paths and SHA-256 hashes, and makes a local frozen commit. `tools/k6_experiment_promotion.py --once` creates a pinned, single-use physical session manifest only if the candidate is tested and frozen. It enrolls the trusted owner’s issue in queue #11 if needed, without reading commands from the issue.
4. Existing locally registered OFFLINE runner: deterministic pinned emulator/simulator tests; never hardware.
5. Existing independent read-only Muse hardware review: must inspect exact manifest and source hash; APPROVED or reject. Source/ABI/watchdogs, dummy load, RF/SDR requirements and complete slot/VFO/PA restore remain required.
6. Exact grant: policy `config/k6-autonomous-radio-policy.json` allows only RX_ONLY or BOUNDED_TX on the dummy-loaded AG7SU bench. One-shot permission is bound to the independently APPROVED manifest hash, specific build/API, expiry, and rate cap. BOUNDED_TX additionally requires actual firmware watchdog ≤1900 ms and fixed permitted test frequency 433.500 MHz.
7. Existing read-only postmortem; publishes findings for research without authorizing repeats.
8. Existing hardware autopilot: verifies live firmware, GH whole-bench ACK, independent review and exact grant; claims the exclusive entire-bench and canonical serial locks; runs each finite step without shell commands; checks exact pre/post module hashes, VFO, TX OFF. Error/timeout/restore uncertainty is **BLOCKED_NO_AUTO_RETRY**, never a silent retransmission. Session evidence and SHA hashes are retained privately.

**Running service:** templates in `systemd/flux-experiment-autopilot.{service,timer}`; installed user service under `~/.config/systemd/user/` with eight ExecStart stages. `systemctl --user daemon-reload` after any edits.

**Command-line:** `python3 tools/k6_experiment_builder.py --preview`, `python3 tools/k6_experiment_promotion.py --once`, `python3 tools/k6_experiment_promotion.py --once --grants`, `python3 tools/k6_experiment_status.py status --json` or `flux-queue status`, and `journalctl --user -u flux-experiment-autopilot.service`.

**Private records:** `~/.local/state/flux-experiment-autopilot/builder.json`, `hardware-generated.json`, `hardware-generated-standing.json`, `generated-manifests/`, and existing attempt/review journals. Pinning and one-shot authorizations are immutable per session. Completed/blocked physical sessions remain consumed even when a later corrected candidate is submitted.

## First complete end-to-end qualification

Issue [#21](https://github.com/tcarwash/flux_radio/issues/21) candidate [`automation/tx28-fastspi-rx-bench-20261124`](https://github.com/tcarwash/flux_radio/tree/automation/tx28-fastspi-rx-bench-20261124) was written by two device-free delegates and independently corrected/tested. Original fast-SPI module performed 384 readbacks; FULL_BENCH implements 10,000 in one finite RX-only module invocation (compiled Cortex-M0 1,420 bytes, bit-level Unicorn modeled 10,000 writes/10,000 reads, 0 errors and complete modeled restore). Twenty offline tests passed. The independent reviewer approved the exact frozen first hardware manifest SHA256 `b0c6fd58...4517`; a fresh GH bench ACK led to a **real one-shot on-radio preflight** on Nov 24. It **BLOCKED before module installation** because the generated RX harness incorrectly assumed packed KUL2 slot images were at most 7168 bytes, but live populated system slots are up to 16384 bytes. The original 31 module hashes and VFO, original build/API, empty slot30 and TX OFF were independently confirmed by the whole-bench locked probe; bench was released. That old grant is consumed forever. Any corrected follow-up must use a new committed source, new manifest and independent review, not repeat the first.

This pipeline is autonomous but not unrestricted. Code-writing agents do not receive device access; only reviewed, pinned and bounded physical controllers may use the dummy-loaded radio. A clean timer alone is not proof the research queue is being worked; status must show intake/build/review/physical states.

## Device-free fixture lane — explicitly separate from hardware queue

Owner instruction (2026-11-24): offline fixtures and emulators do **not** belong
in the physical experiment queue. The local `k6_experiment_sweep.py`
executes 13 explicit research-ref suites in isolated fresh worktrees and
runs their selected test files using pytest with **no USB devices and no
network**, at up to four-way concurrency. Results and source hashes are
stored privately in `~/.local/state/flux-experiment-autopilot/offline-sweep.json`
and individual logs under `offline-sweeps/`; it **never posts to GitHub**, never
claims the radio, never registers a physical manifest, and never reports an
RF PASS. The separate `flux-experiment-fixture-sweep.timer` checks for changed
pinned source SHAs every 15 minutes and skips completed SHAs.

First local sweep: 13/13 branch suites PASS; 295 executed fixture tests;
zero physical radio or bench operations. `flux-queue status` displays these
under **Fixtures** distinct from actual hardware attempts.

The physical queue #11 remains for genuinely necessary bench measurements,
not for repeating Python model tests. A new physical session is added only
after the offline candidate is pinned, independently qualified and assigned
an explicit locally trusted physical harness. All physical results are
reported separately, with live hardware provenance and a real restore check.

Throughput settings: 2-minute physical pipeline timer; up to four active
intake preparation reviewers and 32 assessments/day; 12 new isolated
candidate builds/day; 12 finite reviewed physical one-shots/day. Physical
attempts cannot overlap and are always protected by the whole-bench lock,
TX time-limit, and complete baseline restore/verification. A failed physical
experiment with independently proven session_restore and session_verify
PASS may release the bench automatically (the attempted manifest is still
consumed). An unverified failure keeps the bench held.


Hardware-only intake: the physical service invokes the intake runner
with --once --hardware-only. It no longer submits preparation assessments
for [Flux RF][OFFLINE] issues; these are software research fixtures and
remain in the local sweep. Hardware titles are intake-eligible. An
explicitly prepared local candidate from an older OFFLINE research issue
may be promoted to a physical session only after its tested, pinned
hardware harness exists. That promotion is distinct from fixture results.

## First successful end-to-end unattended physical run (2026-11-24)

Issue #21 (fast-SPI, RX-only capability gate) was independently validated from
candidate commit bdd04879cf03e0695a10b050629b5214e3a76a82. Earlier
KUL2 `-104` was conclusively mapped to wrong envelope authentication:
installed firmware reports cable_only, thus needs a CRC/complement tag
(`key=None`), not SipHash. This was corrected by live capability selection,
with 23/23 isolated harness tests PASS and frozen fresh physical manifest
SHA256 16139ef7df3ed1288c6859d2174ba8a5c1e4af8c4852ba1f3f2dc24b502106d2.
Independent read-only review APPROVED the exact manifest. The recurring
**physical systemd scheduler itself** obtained bench ACK, executed the real
K6 module once and verified all stages. Session
`auto-21-20261124T233851Z`: **PASS**; actual K6 returned exactly
10,000 write/readbacks, zero errors, TX OFF; temporary slot30 removed;
original VFO restored. Job restore/verify both PASS, and independent
session-level complete module-hash/VFO restore and verify both PASS. Bench
released on GitHub #11; owner issue #21 carries hardware result comment
https://github.com/tcarwash/flux_radio/issues/21#issuecomment-7147725799.
Raw private evidence:
`~/flux-bench/batches/auto-21-20261124T233851Z/result.json`.

This proves the guarded RX-only fast-SPI readback primitive, **not** actual
oversampled RRC-shaped 4-FSK transmitted RF. Keep follow-on TX/RRC physical
qualification tracked separately; do not close all of #21's goals based on
an RX-only test. Physical PASS is not an offline fixture PASS.
