# TX-27B: nonzero idle REG51 diagnosis after failed TX-27

**2026-11-24 local · physical issue [#40](https://github.com/tcarwash/flux_radio/issues/40) · no transmitter operation in this diagnostic.**

TX-27 [#20](https://github.com/tcarwash/flux_radio/issues/20) consumed its three physical attempts, none validating a signed-frequency RF output. The first attempt's module returned `-15` (failed internal restore verification), while the latest returned `-13` with the supervisory whole-radio restore and verification reported successful. These are **different failure modes**. The actual bench used `c/k6/mod/tx27_reg51_signed_probe.c` and expected success `22` for 22 symbols; a distinct earlier offline `tx27_reg51_dac.c` returned `90` and should not be confused with the physical test binary.

The flown probe's `-13` is a **self-refusal when a pre-TX snapshot of `REG51 != 0`**, before any intended waveform or `tx_on`. Its synthetic baseline assumed `REG51=0` even though the live firmware evidently initializes the register differently. It does not establish a negative silicon result for a signed frequency DAC. The previous -15 did not establish a safe restore path for nonzero baseline values.

## New stage A: observation only

This change adds `c/k6/mod/tx27b_reg51_idle_diag.c`, an API3 RX-only module that reads the FD6818B chip ID, asserts RF state isn't TX, reads `REG51`, `REG08`, `REG52`, `REG70`, `REG71`, `REG30`, waits 5 ms and reads them again. It reports both `REG51` snapshots and the other tone/synth control words in 4 fixed short lines; a stable **nonzero REG51 is a diagnostic success**, while wrong chip, TX state, pressed key, unexpected radio shutdown or unstable register readings fail closed. Its compiled Thumb body has **no calls to `fd_write`, `tx_on`, `tx_pa`, `tx_off` or direct RF GPIO**. It does not clear or change REG51; no RF waveform is emitted, and it does not imply that an installed-module upload is itself read-only.

`tools/k6_tx27b_idle_bench.py` is a separate one-shot supervisor. The default `--preflight-only` builds the Cortex-M0 module, checks code length (≤1600 B), validates source restrictions and prints source/code SHA256 without touching the radio. `--run` requires explicit `--operator-approved`, **explicit `--issue 40`**, a SHA256-pinned unchanged module source and a unique fresh absolute output prefix. It acquires the shared nonblocking `~/.cache/flux/bench.lock` and the serial lock; verifies enrolled `5d8e7f39844d42f9`, protocol 5, API31 cable-only host (verified in the read-only follow-up; initial `339e08a2b77c7679`/API29 session was BLOCKED_NO_MUTATION before installation), paired VFO A 433.500/B 144.390 MHz FM-wide, RF off and standby idle. It takes a digest of **every original packed module slot**, checks slot30 empty, installs the newly compiled module there via a fresh serial and verified write, executes it once, erases slot30 with a different fresh serial, then compares original hashes, exact VFOs and idle TX-off. Failures are `BLOCKED_NO_MUTATION`, `BLOCKED_RESTORED` or `HOLD_UNVERIFIED` rather than a false PASS. A potentially committed but unacknowledged install is investigated and cleaned up once; an unverified restore writes a persistent `~/.cache/flux/bench.hold` sentinel that prevents subsequent cooperative entry until an independent operator inspection clears it. It never retries the experiment automatically or reuses the consumed #20 manifest.

The included source/CLI and fake-device fault-injection tests exercise wrong-chip and TX refusals, live-like nonzero REG51 values `0x9037`, `0xA03C`, `0x8000`, `0x0300`, wrong issue number, missing operator gate, bad reports, unmatched slot hashes, changed VFO, and held cooperative lock. A fake LiveLink driver additionally validates successful one-shot installation and erase, streamed-only report fallback, uncertain installation acknowledgement recovery, simulated erase failure with persistent `~/.cache/flux/bench.hold` blocking further cooperative bench entry, and no-mutation host enrollment refusal. They use real compiled Cortex-M0 Thumb code under Unicorn, with **zero RFIC writes or transmitter calls**. They do not claim to validate the radio's real register values, host library mutation timing or physical wire state.

## Offline replay

```sh
python tools/k6_tx27b_idle_bench.py --preflight-only
python -m pytest -q tests/test_k6_tx27b_idle_diag.py \
  tests/test_k6_tx27b_idle_bench.py \
  tests/test_k6_tx27b_idle_fake_device.py
```

For an independently reviewed and operator-approved one-shot RX-only physical run, the *owning live bench operator* must confirm current exclusive control of the bench and invoke the new supervisor with a unique session path, e.g.:

```sh
# Only when physically authorized and after a fresh issue #40 review/ACK:
# python tools/k6_tx27b_idle_bench.py --run --issue 40 \
#   --operator-approved --prefix /absolute/new/tx27b-session
```

The supervisor manipulates the **temporary module library** despite the module doing RX-only register reads, so current operator approval, a known original-state snapshot, serial exclusivity and independent postflight verification remain mandatory. The script cannot prove current dummy-load cabling; it must *not* be interpreted as approving RF activity. If cleanup is uncertain, keep the bench unavailable and report HOLD before further hardware use. It writes immutable per-run `manifest.json` and `result.json` evidence in the fresh prefix.

## Next experiment is separately gated

If real `REG51` is stable and nonzero, stage B can explore a **changed transmitter hypothesis**: permit an observed stable baseline, snapshot `REG51` and `REG08` while idle, clear tone/DCS control only with PA explicitly off and a finite TX watchdog, then restore and independently verify every original value. First prove this in the compiled ARM/Unicorn tests with a *nonzero* preflight baseline and deliberately injected restore failure. Do **not** simply remove the `-13` guard and rerun #20; this would ignore its earlier `-15` restoration defect, the PA-on startup window, and the old attempt budget. Stage B requires its own reviewed RF manifest, authorized dummy load/SDR and fresh bench lease.

**Scope:** no Sotto firmware, default production modulation build, host API or hardware RFIC code modified.

## Build-identity requalification after first refused attempt

The first `--run` session `/home/tyler/flux-bench/batches/tx27b-rxonly-20261125T0531Z-887dfb0d` refused **before any library mutation** because the radio advertised build `5d8e7f39844d42f9` instead of the historical `339e08a2b77c7679`. Its report is `BLOCKED_NO_MUTATION`; the unique session is consumed and not reused. A separate locked **read-only** probe saved `/home/tyler/flux-bench/batches/tx27b-readonly-20261125T0532Z-current-build.json` and verified protocol 5, API31 cable-only, VFO A 433.500/B 144.390 FM-wide, standby idle and TX-off, 31 slots with **empty slot30**. This is an enrolled-build update to the RX-only supervisor, not a firmware flash or permission to transmit. Use a **new unique output prefix** for the next one-shot attempt and recheck the bench lock. No other TX-27 circuit hypotheses were changed.
