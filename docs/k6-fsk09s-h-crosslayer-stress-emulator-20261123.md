# Flux FSK-09S-H: emulator stress, compiled-module-to-receiver integration, real archived SDR replay

Date: 2026-11-23. **Offline execution means actual ARMv6-M Cortex-M0 instructions in Unicorn.** This continuation strengthens the prior [FSK-09S-H emulator qualification](k6-fsk09s-h-unicorn-emulator-qualification-20261123.md) without changing the **prospectively frozen armed radio source, its 1,666-byte compiled module or its SHA hashes**. No radio, USB serial, SDR hardware, RF transmission, slot30 installation or firmware flashing occurred. The one-shot B-chip PHY interrupt lease request on [queue #11](https://github.com/tcarwash/flux_radio/issues/11) has **not received separate independent ACK**, so the RF test has NOT run.

## 1. Actual firmware execution under variable SPI and fault timing

`tests/test_k6_fsk_bchip_ae_emulator.py` now invokes the **real compiled API3 Cortex-M0 module** via `test_k6_i2s.run()` with controlled FD6818B SPI register-write latencies of **20, 150, 350 and 800µs**. Under both modeled IRQ encodings (`REG02` bit14 and low-nibble event14), the IRQ onset discriminators pass, the two 49-word TX frames finish, and **no `REG5F` word is written during active TX**. The model drains 16-bit words at 300 words/sec, requires write-zero/read IRQ latching, and uses one stable frame-length guard. Every run verifies preserved RFIC register snapshots and final `tx_off`.

Hardware-abort fault injections: user key-down at several **in-test timestamps** and power switch transition during TX. The real module exits with `-4`, restores original registers and reaches `tx_off`. A key press *after* completed TX is intentionally not a failure; tests select intervals while TX is actually in progress. Additional existing tests cover wrong chip, wrong VFO, absent/early/stuck interrupts, legacy threshold bitfield and never-completing TX (`-14`).

**This does not prove real-B timing.** The emulator computes timing from assumed words-per-second and modeled SPI latency; it does not simulate actual FD6818B FIFO electrical or undocumented state.

## 2. Full cross-layer: compiled ARM -> RFIC model -> noisy IQ -> blind receiver

`tests/test_k6_fsk_bchip_ae_end_to_end_emulator.py` takes the **actual FIFO word arrays captured during Cortex-M0 binary execution**, not a separately authored mock transmitter packet. From each completed frame it creates synthetic complex FSK IQ carrying the standard **public preamble and sync**, with four combinations of the observed B-chip `0x00F8` stray first word: `(normal,normal)`, `(normal,prefix)`, `(prefix,normal)`, `(prefix,prefix)`. These are tested under **both possible IRQ flag encodings**. The existing independent receiver `tools/k6_fsk_bchip_ae_analyze.py` must acquire frames without using payload truth and then accept only the prospectively frozen 48 data words either at byte offset 0 (plus guard) or offset 2 (`0x00F8` then 48 data, with guard displaced).

Eight mixed-layout emulator→IQ→receiver positive cases pass. A **single flipped payload bit** between modeled FIFO and IQ must fail strict RF scoring despite perfectly valid emulator IRQ telemetry; absent AE IRQ must independently fail joint readiness despite a clean RF payload. **Neither a passing model PHY nor a passing model IRQ alone constitutes FD6818B physical qualification.**

## 3. Actual archived RF IQ replay — additional calibration evidence, not FSK-09S-H transmission

A separate **read-only** replay uses the recorded real B-chip SDR capture from FSK-09S-C (`fsk09s-oneword-run01.npy`, SHA256 `7981d591aa03d028f99c6db89e33fa0e13964acf881251951ead62bb2f547ff4`). Independent public-sync-only fixed-4800baud receiver acquires **two real 98-byte frames** with sync correlations `0.995065` and `0.995008`. The first 48 data words in frame 0 match our known vector byte-exactly, but it lacked the **prospectively required new 49th guard word**; frame 1 belongs to a *different* historical vector. Thus the new FSK-09S-H strict score correctly returns **FAIL**, not a retroactive pass. This replay demonstrates the receiver can acquire the same *frame length* from real measured IQ, not that the **new FIFO IRQ code or 24/8 threshold** works on the B chip. Test is in `tests/test_k6_fsk_bchip_ae_archived_rf_replay.py` and intentionally separate from mandatory emulator suite because this historical IQ is available only on the original bench.

## Reproducible strict results

```bash
# Must actually load Unicorn; NO skip of emulator gate; NO network or radio.
./tools/run_fsk09s_h_emulator.sh
# observed: 81 passed, 0 skipped, 0 failed

# Supporting archival RF replay (requires private original bench IQ file):
PYTHONPATH="$PWD:$PWD/tools" python3 -m pytest -q \
    tests/test_k6_fsk_bchip_ae_archived_rf_replay.py
```

**Decision:** FSK-09S-H has demonstrated fail-closed control flow with stronger target-instruction emulator coverage and an archived physical receiver replay. **Next unknown still needs one ACKed B-chip RF trial** to learn whether `REG5E[9:3]` and `REG02` latch actually produce a time-varying FIFO AE interrupt. Do NOT infer a validated >64-word refill grant from the Python register model.
