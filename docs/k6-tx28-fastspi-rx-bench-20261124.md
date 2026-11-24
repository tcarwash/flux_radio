# TX-28 RX-Only 10,000-Readback Bench Harness (Candidate, Unproven)

**Date:** 2026-11-24. **Issue:** #21 (queue AG7SU K6 Flux experiment).
**Scope:** candidate harness only. No radio contact was made during
development; no physical testing is claimed. The radio (build
`339e08a2b77c7679`, API29) is supervisor-controlled on a dummy load.

## Files

- `tools/k6_tx28_fastspi_rx_bench.py` — the harness (stdlib only at import;
  hardware modules imported lazily inside `--run`/`--verify-only`).
- `tests/test_k6_tx28_fastspi_rx_bench.py` — mocked unit tests, no hardware.
- `c/k6/mod/tx28_fastspi_probe.c` — NOT modified. The existing compiled
  1412 B bench module is preferred unchanged.

## CLI

- `--preflight-only [--prefix DIR]`: deterministic local validation only
  (source, argv, ABI=1, level 2, inert default, expected build). No port.
- `--run --dummy-load-confirmed --operator-lease-confirmed
  --queue-lease-acknowledged --prefix ABS_PATH`: one finite guarded attempt.
- `--verify-only --prefix ABS_PATH`: read-only recheck (no TX, no drift,
  exact-10k hardware record). Never mutates the radio.

Guards: TX/idle check before launch, VFO + all 32 packed slot-blob backups,
temporary slot 30 only with restore + readback verification, host watchdog
(`RUN_TIMEOUT_S` = 600 s, single attempt, no retry), module key/power abort,
TX-off recheck on every exit path including interrupts.

## FULL_BENCH variant (10,000 in one invocation)

`c/k6/mod/tx28_fastspi_probe.c` now has a compile-time `TX28_FULL_BENCH`
variant: 5 ladders x 250 reps x 8 patterns = **exactly 10,000** verified
RX-only write/readbacks in ONE invocation (keeps the 5 slowest/safest NOP
values 32/16/8/4/2). The default bench build is unchanged at **384**
(6 x 8 x 8, keeps nop=1 timing proof); the inert default
(`TX28_BENCH_ENABLE=0` -> -9), API level 2, TX/PA/FSK refusals, per-loop
key/power watchdog, and GPIOB/TIM17/REG_5A save/restore on every exit are
identical in both paths. Timing averages divide by the actual per-ladder
write count (no overflow: worst case ~4M cycles, far below 2^32).

Builds: inert default (`-9`), bench 384 (`TX28_BENCH_ENABLE=1`), full-bench
10000 (`TX28_BENCH_ENABLE=1` + `TX28_FULL_BENCH=1`, compiled by the bench
wrapper only). `--run` unblocks only via the FULL_BENCH path and still
aborts with exit 3 before any port contact when no 10k path exists
(`required_change` recorded). Host `RUN_TIMEOUT_S` = 60 s, single attempt,
no retry. Counts are never fabricated by looping invocations or scaling
384-op results. No physical 10k result is claimed here; only the
supervisor-driven bench run with live-hardware provenance can PASS.

## Scoring

Physical PASS requires exact `ops == 10000`, zero errors, fully preserved
state, and live-hardware provenance. Mock/offline/replayed data is never a
physical PASS regardless of counts.
