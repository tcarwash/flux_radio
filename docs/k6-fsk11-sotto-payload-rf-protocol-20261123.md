# Flux FSK-11 — precommitted physical two-Sotto-packet native-FSK test

Date: 2026-11-23, before physical RF emissions. Branch `research/offline-sotto-fsk-dv-20261123`.
User-authorized, conducted/dummy-loaded K6/FD6818B test with air-coupled RTL-SDR. Requires nonblocking shared Flux serial-port lock, identical transaction safety to FSK-10, and verified original module/VFO restoration. The active TX16N agent retains use of the radio between our short guarded transactions.

## Test hypothesis

The two known **CRC-valid 64-byte Sotto 1200 L packets** from the existing 15-second Sotto-1200 sample can be sent as 32 native 16-bit FIFO words each at 4,800 raw bits/sec. An independent offline SDR I/Q decode that learns its clock/levels using only each packet's first 256 bits should reproduce **all 64 bytes** of both packets exactly, with the final 256 bits of each held out. Those recovered, **not copied**, bytes must pass the existing `pager.profile('L','1200',depth=1)` CRC/whitening parser, yielding two consecutive packet sequence numbers and 16 encoded Sotto superframes (640 ms of compressed speech data).

No audio PCM synthesis is attempted on the K6, and no FIFO streaming or real-time low-latency speech is implied by success. This is a **two-packet transport validation**; acquisition is test-pattern/pilot-assisted, not blind sync.

## Frozen and reproducible sources

- Actual codec data: `sotto/docs/samples/rates/male_libri3436_15s__1200.bit` from existing checked-out Sotto library, input SHA-256 `6c3b4ac8905d287ff4b2e52cc49b7d97b31ed664e4aa69dcb6d2cfe5997b2d02`.
- `tests/data/k6/fsk11_sotto_l_payload_manifest.json`: two exact 64-byte whitened `pager.Profile('L')` packets with msg_id 33, sequences 0 and 1, original Sotto frame bodies and packet CRC16.
- Packet 0 SHA-256: `9228c7481890b0f8caf21f07fe8d70b978bf5be1b14d3e3ffb71c28de7dd2f4a`.
- Packet 1 SHA-256: `03202f23d9f78ce8af6ed749910087294abd6fc1579d7b7d1e2e8e9ba8bffb7c`.
- Inert source: `c/k6/mod/fsk_sotto_l_payload.c` (TX disabled unless explicitly rebuilt with fixture/watchdog macros).
- **Frozen armed source**: `c/k6/mod/fsk_sotto_l_payload_armed.c`, SHA-256 `08b02d1a437096d3b260e8d7d043ed2d7de5f872e04bab60fae3340823e4a657`.
- Compiled module SHA-256: `2f042fe965d30f0972b80dc61166d03b887902bc556c7557f0434ac7deeb0224`, 1080 bytes, level3, entry zero.
- Bench wrapper `tools/k6_fsk_sotto_payload_bench.py` fails closed unless compiled and source hashes match, both packets validate against the existing CRC parser, and the operator has explicitly confirmed the dummy-load arrangement.
- Offline RF/packet decoder: `tools/k6_fsk_sotto_payload_analyze.py`. Unlike FSK-10's truth-only BER scorer, this regenerates **received bytes from SDR symbol decisions** and feeds them to the actual Flux L parser.

## Guardrails

Pre-existing radio fixture only: 433.500MHz TX (dummy load), 433.750MHz RTL-SDR @1.024 Msps; expected firmware build `95d6b260d4122287`, silicon 0x4829, original slot30 PDM code and VFO 144.390MHz RX idle. The shared `serial_lock('/dev/ttyUSB0')` protects the **entire** module backup, temporary installation, one <=1900ms watchdog-limited transmission, IQ capture, restoration, signed library readback and VFO restoration. Do not force a busy lock. No MCU firmware flash, calibration writes, automatic retries or long-running keyed transmitters. A missing original, abnormal firmware, unexpected module, occupied channel, or failed restore is a hard stop.

## Predeclared scoring

Pass is strictly: (1) both 32-word frames return 64 total; (2) both independent training fits lock with correlation >=0.93; (3) both **256-bit heldout tails have zero bit errors**, and recovered 64-byte payloads match their independently frozen SHA256; (4) CRC16/sequence parser accepts each reconstructed byte stream and assembles 16 real Sotto compressed frames; (5) the exact original slot code/VFOs restored and TX idle; (6) measured start separation and nonpayload time logged. If any criterion fails, mark INCONCLUSIVE/FAIL, keep IQ/report, do not rerun blindly.

Only **one physical session** is planned after offline test suite, commit and push. Additional repeats require a fresh, explicitly separate bounded run.

Commands (execute only after physical lock can be obtained without force):

```bash
python3 -m pytest -q tests/test_k6_fsk_sotto_payload.py tests/test_k6_fsk_sotto_l_lab.py
python3 tools/k6_fsk_sotto_payload_bench.py \
 --prefix /home/tyler/flux-bench/tx-injection-20261122/fsk11-sotto/fsk11-sotto-run01 \
 --dummy-load-confirmed
python3 tools/k6_fsk_sotto_payload_analyze.py \
 --iq /home/tyler/flux-bench/tx-injection-20261122/fsk11-sotto/fsk11-sotto-run01.npy \
 --out /home/tyler/flux-bench/tx-injection-20261122/fsk11-sotto/fsk11-sotto-analysis-run01.json
```
