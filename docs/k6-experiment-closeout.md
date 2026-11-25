# Flux experiment closeout and searchable archive

GitHub's issue list was 27 open / 2 closed on November 24, 2026 before the archive migration. The goal is to keep open GitHub items limited to genuinely outstanding physical work and coordination; offline work belongs in the local shared results ledger.

The host command `flux-issues` searches GitHub open, closed, or all states, e.g. `flux-issues search --state closed --query REG40` and `flux-issues search --state all --query Sotto`. Native GitHub search also works with `is:issue is:closed repo:tcarwash/flux_radio`. The linked content, issue comments, experiment reports and commit hashes remain available after closure; closing does not delete evidence.

Software-only findings, failures and pending offline investigations are searchable via `flux-offline-results list --query REG40` and `flux-offline-results list --status REGISTERED`. The database remains local to the shared workstation; agents need access to that machine to search it.

### Closing a finished experiment

An owning agent first checks the issue's original acceptance criteria and evidence. Distinguish successful simulation from actual radio proof, and ensure RF postflight has independently verified restoration. If a historical OFFLINE scope is actually fulfilled, run a preview:

    flux-issues close --issue 16 --disposition completed \
      --summary "Offline DTMF source audit and model passed; B-chip RF not yet proven." \
      --evidence https://github.com/tcarwash/flux_radio/commit/de2c3f1 --apply

Omit `--apply` for a preview. A completed closeout posts a durable evidence comment and closes the issue with GitHub reason `completed`. The outcome remains searchable. Do not automatically close an issue merely because a fixture or physical session returns PASS; a large issue may have unfinished subgoals.

### Moving legacy unfinished offline issues out of GitHub

For a historical OFFLINE-only investigation that still needs work, archive with `--disposition migrated`, and a summary describing its open questions and evidence. This creates a shared ledger record `legacy-issue-N` in REGISTERED state, posts the migration evidence comment and closes the old GitHub ticket as `not planned` for tracking. It **does not claim** the experiment passed or finished. Agents query the ledger, run their own device-free experiments, and publish results there. Original issue comments and all links remain archived and searchable.

The archive helper rejects GitHub coordination #11, non-Flux issues and issues with enrolled hardware candidates. For a fully completed hardware experiment, the owning agent must check the *whole issue* acceptance criteria, verify independent radio restoration, and provide both result evidence and a restoration comment URL, for example:

    flux-issues close --issue N --scope hardware --disposition completed \
      --summary "Exact hardware goal met; signal metrics and baseline verified" \
      --evidence https://github.com/tcarwash/flux_radio/issues/N \
      --restoration-evidence https://github.com/tcarwash/flux_radio/issues/11#issuecomment-N \
      --acceptance-reviewed --apply

The supervised hardware execution system and lock are unaffected. Historical fixture results cannot force hardware closure.

### Limits

This is a supervised/agent-initiated closure policy, not an automatic blanket closure on each PASS. Full issue success criteria and physical verification cannot be reliably inferred from a terminal stage alone. Future agents should perform the closeout when their scoped work really finishes. `flux-issues` is read-only unless an agent supplies `close ... --apply`.
