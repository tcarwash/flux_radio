# Flux FSK-09S-E — prospective 48+4+4+4+4 native FIFO live appends

Date 2026-11-23. **Freeze source/acceptance/tests BEFORE any RF**. Branch `research/fsk09s-single-word-append-20261123`; active shared experiment #9. Based on [physically successful single-word append](k6-fsk09s-single-inflight-success-physical-20261123.md) and explicit caution that this still does **not** prove sustained FIFO refill beyond demonstrated preload capacity.

## Hypothesis and exact one-frame setup

FD6818B can accept **multiple** in-flight FIFO writes without stopping the 4,800-bps native-FSK modem. The test programs exactly **64 FIFO words** in one finite frame (`REG_5D=0x7F00`), preloads **48 words**, starts FSK TX, then appends **four independent batches of four words** once the module clock reaches at least **70, 95, 120 and 145ms** from the TX start register write. The four batches are contiguous deterministic words 48–63 of the already validated FSK laboratory vector. The total number of FIFO writes is **64**, no more than the **64-word preload** physically validated previously on this exact RFIC. This explicitly avoids reliance on unverified FIFO headroom registers, an unobserved interrupt, destructive `REG_5F` reads, or guessing the number of words already consumed.

This is ONE hardware frame, NO modem rearm, no extra filler frames and no hidden source audio. Known universal native-FSK hardware preamble and sync are 16×0x55 and `07 6B 3E 36`. Data is different from preamble and includes many randomized 16-bit tail words for independently verifying each append batch.

## Predeclared pass/fail

- Device must report frame completion **64** within the firmware's **1900ms** TX watchdog and **700ms** frame poll limit; initial signed slot30 module and both initial 144.390MHz VFOs must be restored and independently read back; final TX off. Failure here is an immediate safety stop, no further RF test.
- **Independent SDR capture**, 433.750MHz center/1.024 Msps, air-coupled to dummy-loaded K6 on 433.500MHz, must detect **one** native FSK frame solely from hardware preamble/sync. `tools/k6_fsk_fourbatch_analyze.py` does not use the expected FIFO payload for synchronization.
- Require **zero bit errors across all 1024 RF payload bits** and, explicitly, **zero bit errors over appended words 48–63 (256 bits)**. Device completion alone is not a valid pass. Correlating to known payload or tolerating shifted data is not allowed as success.
- Any missing frame, mismatch in any append batch, timeout, or unexplained status becomes **INCONCLUSIVE/FAIL**, with raw IQ and full restoration/transaction JSON retained. No automatic retries, no extra append or register experimentation in this transaction.
- Even a complete pass establishes **16 additional words in four timed writes while active**, not more than 64 total FIFO words, no hardware status headroom semantics, no indefinite 4800-bps byte stream or arbitrarily constructed IQ.

## Hardware safety and exact frozen source

- User-confirmed conducted/dummy-load K6 RFIC 0x4829, firmware build `95d6b260d4122287`, original trusted slot30 PDM module. No main firmware flash or calibration changes. PA keyed only for one short finite frame and bounded by existing hardware-independent timeout.
- Canonical nonblocking Flux `serial_lock('/dev/ttyUSB0')` must be owned from preflight through temporary signed module install, one TX + SDR capture, strict slot30 backup/restoration/readback, return to both 144.390MHz VFOs, final TX idle. Busy lock aborts. No other RF agents' worktrees touched.
- Inert source `c/k6/mod/fsk_fourbatch_append.c`, frozen armed source `c/k6/mod/fsk_fourbatch_append_armed.c`.
- **Armed source SHA256** `79f545d65f6006a34d511679d644429c383cb1bef4cc7970729fce7cb320ed8e`.
- **Compiled code SHA256** `a75d0bf0330ffda460c13ef30165ecad2fe02bf9de8a3412b55be51c4cd074c4`, module 1244 bytes, API level3, entry zero.
- Frozen bench wrapper `tools/k6_fsk_fourbatch_bench.py`, decoder `tools/k6_fsk_fourbatch_analyze.py`, safety tests `tests/test_k6_fsk_fourbatch.py`.

## Single physical transaction commands

```bash
python3 -m pytest -q tests/test_k6_fsk_fourbatch.py tests/test_k6_fsk_oneword_single.py
python3 tools/k6_fsk_fourbatch_bench.py \
 --prefix /home/tyler/flux-bench/tx-injection-20261122/fsk09s-fourbatch/fsk09s-fourbatch-run01 \
 --dummy-load-confirmed
python3 tools/k6_fsk_fourbatch_analyze.py \
 --iq /home/tyler/flux-bench/tx-injection-20261122/fsk09s-fourbatch/fsk09s-fourbatch-run01.npy \
 --out /home/tyler/flux-bench/tx-injection-20261122/fsk09s-fourbatch/fsk09s-fourbatch-analysis-run01.json
```

This experiment is only a finite-capacity follow-up to the demonstrated in-flight 1-word success. Any >64-word FIFO-drain/streaming experiment must be designed and committed separately with a defensible independent headroom/timing criterion.
