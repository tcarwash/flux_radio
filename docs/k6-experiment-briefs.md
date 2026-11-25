# Start a Flux experiment with a short GitHub brief

A hardware experiment no longer needs a prewritten bench harness, delegate
recipe, or complete physical validation protocol at submission time.

Create an issue in tcarwash/flux_radio, authored by tcarwash, with a title like:

    [Flux RF][EXPERIMENT] Does REG40 alter native FSK deviation?

An owner-authored title beginning with [Flux RF][HARDWARE] also qualifies.
Other older/DEFERRED/OFFLINE issues are **not** silently converted into
hardware requests. The alternative is a [Flux RF] title with the label
experiment:brief. For software-only experiments, use flux-offline-results
instead of GitHub.

The issue body can be normal prose. Field headings are helpful but optional.
For example:

    Goal: Determine whether REG40 modifies deviation during B-chip FSK.
    Method: Compare two 33-word finite FSK bursts on the dummy-loaded K6,
    measuring received instantaneous frequency with RTL-SDR.
    Firmware API: 29
    Placement: loadable module
    Expected: A reproducible deviation difference between the two bursts,
    or a supported negative observation.

The same information in two or three ordinary paragraphs is also fine.
Only a meaningful ~40-character description is required. The agent can
derive missing details, choose measurable criteria, and ask for independent
confirmation rather than making up success.

## What the pipeline now does

1. Every hardware or candidate timer cycle, the brief registrar reads open
   issues from GitHub and checks the issue AUTHOR against tcarwash.
2. A qualifying brief becomes a local structured record. A loadable-module
   proposal gets a deterministic candidate recipe using origin/main, with
   precisely four generated paths scoped to that issue:
   c/k6/mod/k6_exp_N_probe.c, tools/k6_exp_N_bench.py,
   tests/test_k6_exp_N_bench.py, and docs/k6-exp-N-brief.md.
3. The isolated device-free delegate turns the objective/method/expected
   result into executable code, a repeatable offline test, and a bounded
   physical harness. The authoring step has no radio, serial, USB or SDR.
   Failed local checks go to bounded device-free repair, not hardware.
4. The supervisor runs tests, freezes reviewed source and generates the
   one-shot manifest. Only then can the existing independent review, exact
   grant, bench lock, finite TX watchdog and postflight authorize radio use.
   The three-physical-attempt limit remains in effect per issue.
5. A valid report explains what was directly measured, not merely whether
   the compiled test suite passed. See k6-experiment-evidence-levels.md.

This is a **candidate bootstrap**, not permission to execute arbitrary shell
commands from an issue. The owner is trusted to set goals; newly generated
firmware/scripts are still treated as unverified code until qualified.

For firmware placement specified as on-flash (not a loadable module), the
brief is kept as FIRMWARE_REVIEW_REQUIRED, with no automatically executable
module recipe or flash permission. A firmware-capable agent must resolve
its distinct build/flash and restoration requirements before hardware work.

The local registry is private:
~/.local/state/flux-experiment-autopilot/brief-recipes.json

Agent commands:

    python3 tools/k6_experiment_brief.py --status
    python3 tools/k6_experiment_brief.py --sync
    flux-experiments status

No new Github issue is needed for a corrected *offline fixture*. For
hardware experiments that exhausted three failed physical attempts, file a
new issue with materially changed parameters, link the original and
preserve the past results.
