# FSK-09S-G — frozen prospective fully preloaded 60-word control

Date: 2026-11-23. **PREPARED AND OFFLINE TESTED; PHYSICAL RF NOT RUN.**
Isolated branch `research/fsk09s-frame-audit-20261123`. Shared
coordination: [#15](https://github.com/tcarwash/flux_radio/issues/15)
(frames/root-cause) and [#9](https://github.com/tcarwash/flux_radio/issues/9)
(FIFO append/streaming).

## Discriminating hypothesis

Earlier FSK-09S-F (48 preload + three 4-word batches) was decoded from real
SDR IQ at fixed 4800 baud as **0x00F8 followed by all 59 intended words that
fit inside its 60-word declared frame**, with zero errors after the post-hoc
one-word displacement. The additional reported bit slips came from a
4,790.4-baud decoder candidate, not from confirmed RF symbol slips.

This test asks whether `0x00F8` appears **without any mid-TX FIFO writes**,
when all 60 native-FSK payload words are preloaded before starting TX.

- **Single bounded native FSK frame**, **60 words / 120 bytes / 960 bits**.
- **All 60 FIFO words** are loaded before `REG_59=0x08F8`.
- Same B-chip (0x4829) modem frequency (433.500MHz), gain code 21, baud
  code 0xC194 (4800bps), sync, preamble, 60-word declared `REG_5D=0x7700`,
  `REG_59` reset and FIFO clear sequence as the failed F case.
- No writes to REG_5F after start; no REG_5F destructive read, no uncertain
  REG_02 almost-empty interrupt, no REG_5E threshold manipulation.
- One standalone frame; never more than 60 FIFO writes (within physically
  demonstrated 64-word preload).
- **No retransmission** on failure. This experiment tests initialization
  versus live-write interference; even success is not continuous streaming.

## Frozen artifacts (commit these *before* physical execution)

- Inert-default source: `c/k6/mod/fsk_preload60_control.c`.
- Armed source: `c/k6/mod/fsk_preload60_control_armed.c`.
  Source SHA256: `c3982e2a553949472d6aef6d84d7c415dbfec3820ffc6004b6b002237ad0e27e`.
- Compiled armed API3 module: **1056 bytes**, entry 0.
  SHA256: `566d23eb2211bad873e671302f3d06770a454acdca0ce40a5c66b216d2b2c217`.
- `tools/k6_fsk_preload60_bench.py`: checks both hashes, refuses without
  **--dummy-load-confirmed** and **--operator-lease-confirmed**, delegates
  hardware transaction to existing lock-guarded signed-slot backup / VFO
  restoration harness; 1900ms watchdog and 700ms frame timeout.
- `tools/k6_fsk_preload60_analyze.py`: independent public framing receiver
  with **fixed** nominal 4800baud (frozen before RF), no payload-assisted
  acquisition; exact entire-frame comparison, no shifted success.
- `tests/test_k6_fsk_preload60.py`: offline preflight and strict decoder
  negative control against archived F failed capture.

## Prospective acceptance

A **PASS** requires all of:

1. Explicit bench operator permission and exclusive radio/SDR lease; the
   canonical Flux nonblocking `serial_lock('/dev/ttyUSB0')` acquired for
   the entire transaction. Validate radio identity/firmware, original slot30,
   both VFOs, RX/PA safe state, SDR baseline, physical dummy load and
   independent short TX/watchdog restoration before module loading.
2. One and only one `REG_5F` FIFO payload run: the complete 60 words were
   written before TX starts. Module returns **60**, watchdog and current
   safety flags pass. Record exact code/source SHA, transaction and IQ hashes.
3. **Exactly one** public preamble/sync acquired 120-byte frame, complete
   RF payload exactly equals all 60 expected words: **0/960 bit errors**,
   first word 0x5555 (LE `55 55`), and final seeded tail marker exact.
   No expected payload bits used for receiver acquisition.
4. Original signed module slot and both VFOs are independently read back
   and verified, radio RX idle, PA/TX off; hardware lease released.
5. Treat **any** extra leading 0x00F8 or missing last word as **FAIL**,
   even if later post-hoc displacement matching produces zero errors.

A clean frame proves only that **this standalone full-preload startup
path** did not reproduce the artifact; it does not determine why the
E/F append runs did. A failed frame shows that **in-flight appending is
not necessary** for the artifact. Either result provides a useful next
branch for source investigation.

## Commands (only after explicit hardware authorization)

Offline preflight only:

```sh
python3 -m pytest -q tests/test_k6_fsk09s_frame_audit.py tests/test_k6_fsk_preload60.py
```

Hardware transaction, **not executed in this preparation**:

```sh
python3 tools/k6_fsk_preload60_bench.py \
  --prefix /home/tyler/flux-bench/tx-injection-20261122/fsk09s-preload60/fsk09s-preload60-run01 \
  --dummy-load-confirmed --operator-lease-confirmed
```

SDR-only postflight (after the guarded transaction produces its IQ file):

```sh
python3 tools/k6_fsk_preload60_analyze.py \
  --iq /home/tyler/flux-bench/tx-injection-20261122/fsk09s-preload60/fsk09s-preload60-run01.npy \
  --out /home/tyler/flux-bench/tx-injection-20261122/fsk09s-preload60/fsk09s-preload60-analysis-run01.json
```

**Next gate after strict clean control (not pre-authorized):** a separately
frozen 48 + two distinct 1-word timed writes (50-word declared frame) to
distinguish single from repeat append. Stop if control reproduces 0x00F8,
and audit REG_59 / REG_5F B-silicon clear/start ordering before considering
more RF tests. Never infer safe >64-word headroom or streaming from these
bounded experiments.
