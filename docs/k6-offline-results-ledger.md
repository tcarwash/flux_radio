# Shared offline experiment results (agent workflow)

Offline software experiments are **not** GitHub issues and do **not** consume
the K6 hardware bench. The shared host database is:

    ~/.local/state/flux-experiments/offline-results.sqlite3

Use the host command flux-offline-results from any connected agent thread.
It is a small Python/SQLite result registry, not a scheduler or test executor.

Before doing work, search recent evidence:

    flux-offline-results list --query "Sotto"
    flux-offline-results list --status FAIL
    flux-offline-results show <result-id>

A new device-free experiment is independently executed by its agent. Register
it before running, using the actual checked-out commit, and finish afterward:

    flux-offline-results register --name "Sotto 1200 codec loopback" \
      --owner "agent-name" --sha "$(git rev-parse HEAD)" \
      --ref "$(git branch --show-current)" --test tests/test_sotto_loopback.py

    # Run the software tests, model or emulator independently; no radio involved.
    flux-offline-results finish <result-id> --status PASS \
      --summary "1200-baud synthetic frames recovered; real RF not measured" \
      --metrics-json '{"tests_passed": 9, "frame_errors": 0}'

Record failures and blocked tests too. Include useful metrics, source revision,
test targets, limitations, and optionally a log path and SHA256 receipt.
Finish is immutable: create a new record for a rerun, never overwrite a prior
result. The CLI only registers and reports evidence; it **never executes tests**.

The existing k6_experiment_sweep.py 15-minute fixture timer automatically
syncs completed receipts from its private state into the shared ledger,
idempotently. An initial backfill can be repeated safely:

    flux-offline-results import-sweep

Historical fixture receipts happen to contain old GitHub issue numbers as
local keys. The shared ledger uses independent sweep-... IDs based on test
targets and source commits; it never posts to, creates, or enters GitHub
issues. The corresponding original GitHub source reference is not required
for new tests.

This SQLite file lives on the one shared development host. It is visible to
agent threads connected to that host and does **not** replicate automatically
to GitHub or other machines. Use existing remote-dev shell/file access to
query it. If cross-host replication is desired, that is separate work.

Do not confuse offline PASS with hardware qualification or RF transmission.
The separately locked, supervised physical experiment workflow remains
unchanged.
