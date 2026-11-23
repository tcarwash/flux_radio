# Flux FSK-09S-F — prospective 60-word, three-batch mid-TX FIFO append test

Date 2026-11-23. **Must be frozen and committed before physical RF.** Dedicated experiment `research/fsk09s-single-word-append-20261123`, shared queue #9. Separate from the failed strict 64-word FSK-09S-E test, in which an extra leading `0x00F8` word displaced the final scheduled word despite independent RF decoding of the other 63 payload words, including 15 in-flight-written words. See [E partial report](k6-fsk09s-fourbatch-physical-partial-20261123.md).

## Hypothesis and controlled change

The FD6818B can accept **three distinct four-word append batches** during native 4,800bps FSK TX, while staying **four words below** the physically demonstrated 64-word preload capacity. The single finite hardware frame is programmed for **60 words/120 bytes**, `REG_5D=0x7700`. Initial preload **48 words**, then exactly **4 words at >=70ms**, **4 at >=95ms**, and **4 at >=120ms** after starting TX. No prior modem frame/rearm, no MAC/voice payload, no register IRQ or guessed headroom, no destructive FIFO read. Max written after one FIFO clear is **60/64 words even if none have drained**.

The data is the deterministic Flux FSK lab vector, first **60** words of the previously used seed 0xACE1. The appended 12-word tail is diverse and independently post-hoc checkable. We do not change gain, rate, sync, timing, or TX watchdog other than shortening the declared FIFO length from 64 to 60 and eliminating the last four words/batch from the *separate* previous test.

## Strict prospective pass/fail

1. Bench harness must acquire the **canonical nonblocking shared Flux serial lock**, identify 0x4829 chip/build `95d6b260d4122287`, original signed slot30 PDM module, both original VFOs 144.390MHz RX idle, suitable RSSI channel and SDR baseline; then record the exact signed backup before temporary module modification.
2. Single RF invocation at dummy-loaded 433.500MHz, RTL-SDR centered 433.750MHz 1.024Msps, with **1900ms hard TX lease** and **700ms module frame timeout**. No automatic retries or main MCU flash. Require module report **60** and signed slot30 restore/readback, VFO restoration and TX off even on negative decoder results.
3. Independent public-sync receiver MUST locate **exactly one** 120-byte native FSK frame from the chip's universal 16×0x55 preamble + four-byte `07 6B 3E 36` sync, without training on any known test data. Decode the entire RF payload to bytes independently of expected truth. Strict PASS requires **zero bit errors in all 960 RF payload bits**, including **zero errors over the final 192 bits (12 words) written after TX start**. A firmware return code is not sufficient.
4. If an extra leading `0x00F8` appears, one-word displaced traffic, missing final word, incomplete module result, or any other mismatch, result is strictly **FAIL/INCONCLUSIVE** regardless of any promising post-hoc realignment. Preserve IQ, raw decoded words, and transaction metadata for diagnosis.
5. Even success here proves **three timed append batches totaling 60 words**, not refill beyond the FIFO's proven capacity, a FIFO headroom IRQ, indefinite streaming or arbitrary RF output.

## Fixed source and safe commands

- Inert C: `c/k6/mod/fsk_threebatch_append.c`; exact armed C: `c/k6/mod/fsk_threebatch_append_armed.c`.
- **Armed source SHA256** `29934e5fec2ff127b5026e64e864b76b325813172bfc8d92986960bd3091f10f`.
- **Compiled module SHA256** `ac75d540231ebd071f36dde4d6f2c6108d06190de98d2b65cfd476bf573b04d5`, 1244 bytes, API level3, entry 0.
- `tools/k6_fsk_threebatch_bench.py` verifies source/code hash and hardware safety then delegates entirely to established transaction/restore/lock. `tools/k6_fsk_threebatch_analyze.py` reads IQ offline and uses only known public sync for demodulation. `tests/test_k6_fsk_threebatch.py` checks source invariants.

```bash
python3 -m pytest -q tests/test_k6_fsk_threebatch.py tests/test_k6_fsk_fourbatch.py
python3 tools/k6_fsk_threebatch_bench.py \
 --prefix /home/tyler/flux-bench/tx-injection-20261122/fsk09s-threebatch/fsk09s-threebatch-run01 \
 --dummy-load-confirmed
python3 tools/k6_fsk_threebatch_analyze.py \
 --iq /home/tyler/flux-bench/tx-injection-20261122/fsk09s-threebatch/fsk09s-threebatch-run01.npy \
 --out /home/tyler/flux-bench/tx-injection-20261122/fsk09s-threebatch/fsk09s-threebatch-analysis-run01.json
```

This is a separately prospective test to isolate a possible 64-word FIFO full/boundary artifact while not depending on unverified silicon headroom. The user-confirmed conducted dummy-load setup stays mandatory. No repeat or further TX automatically authorized if this fails.
