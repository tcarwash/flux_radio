# Goal: live RF Sotto TX/RX qualification, API31 (2026-11-25)

Status: **qualified for the bounded 320ms generated-tone test path**, not yet
qualified for human microphone/live full-duplex or for the original
API31 asynchronous-FSK callback. All hardware work used the K6 on the
operator-confirmed dummy load, VFO A 433.500 MHz, PL2303 live serial and
RTL-SDR air-coupled receiver.

## Goal outcome

The receiver now acquires continuous SDR USB data on a dedicated thread,
separate from demodulation. This resolved systematic corruption at RTL
128ms bulk-read boundaries, which had misleadingly appeared to be
transmitter/FIFO packet-tail truncation. Then an API31 *on-radio* 1kHz
Sotto tone encoder was qualified using a known-good B-chip RFIC
sequence: one bounded TX lease, PA enabled for FIFO preload, 20ms
settling before each FSK frame, PA maintained during the three-frame
session, zero inter-frame gap, and finally PA off/register restoration
and RX return.

**No MCU firmware was flashed, and no band limit or TX watchdog was
removed or relaxed.** A temporary slot27 module image was installed with
readback verification; source is on the research branch.

## Physical live evidence

### Receiver acquisition

Original monitor interleaved synchronous RTL SDR USB reads, FFT,
demodulation and codec work. Seven of eight old corrupted frames began
to diverge near 128ms read boundaries. The same old FSK-13 raw capture
decoded 8/8 correctly offline. Switching to 433.750 MHz center and
0dB fixed gain alone did **not** fix the live monitor (0/8).

The fix in `/storage5/dev/sotto-monitor/app.py` is a dedicated raw
USB reader thread, bounded 24-block queue, explicit overrun errors and
`SOTTO_RTL_IF_OFFSET_HZ` center override. On the current firmware:

| Receiver / physical control | CRC-valid / expected | CRC-rejected |
|---|---:|---:|
| Frozen FSK-13, pre-fix live monitor | 0 / 8 | 8 |
| Same frozen FSK-13, raw SDR recorder | 8 / 8 | 0 |
| Same frozen FSK-13, new live monitor | 8 / 8 | 0 |

The new monitor ran without queue overflow (peak 1), with all eight
FSK-13 syncs exact.

### Known tone physical link

On-radio tone: 1kHz, 320ms, PCM16 at 8kHz, eight 6-byte codec frames,
FDV1 BEGIN → VOICE → END, three guarded 66-byte physical packets at
4800 bit/s. Codec source pin:
`598f1bce87c22ebada2b9d0d1fcfd89107b33a09`.

Qualification module `SOTTO PCM RF`, API31, 9,863 code bytes (SHA256
`c44f39c769d68f0584f993c758bcd4935334d1a02d0f196bc591fb8577536eaf`).
It encodes PCM with typed host resources while the radio remains in RX,
then starts TX and sends exactly three frames.

| Configuration / experiment | Physical result |
|---|---|
| Native API31 `fsk_async`, PA muted during FIFO preload | VOICE+END CRC-valid; BEGIN not seen |
| Extra 20 guard bytes, RFIC direct preload | Invalid CRC, earlier corruption |
| Partial FIFO preload + in-flight refill | No decodable frame candidates |
| Direct RFIC with PA muted (even with 20ms delay) | No valid frames |
| PA enabled during FIFO preload, 20ms settle +60ms inter-frame gap, prerecorded voice | 5/5 full sessions, 15/15 CRC-valid, zero rejects |
| PA enabled, zero settle + zero inter-frame gap, direct fixture | A valid full session; not repeatability qualified |
| PA enabled, re-key between frames | BEGIN rejected or missing |
| On-radio PCM encoding, PA enabled, zero settling, no gap | 3/4 full sessions; one BEGIN CRC reject |
| **On-radio PCM encoding, PA enabled, 20ms settling, no gap** | **6/6 full sessions, 18/18 CRC-valid, zero rejects, zero sync errors** |

The six on-radio receiver voice events all decoded into exactly
**2,560 samples (320ms)** identical to the independent reference:
codec SHA256
`d0dcef27d3113e2d297c23ba79a873b80a81c9940618f2323ca57c0800a464ad`;
PCM SHA256
`3e00ae265f669c1c39173d4c47307c8999b1fcfcd33bc8cf648758b068410bfd`.

### TX control / failure recovery

- Token-bound cooperative cancellation during codec encode: acknowledged,
  module returned -15, zero additional RF packets, idle RX.
- Cancellation while transmitting: acknowledged, module returned -15
  after two CRC-valid packets, PA de-keyed, idle RX.
- Immediately following normal session: 3/3 valid packets and idle RX.
- Hamlib loopback bridge with one serial owner (port 4533 for this
  session; default port 4532 occupied by separate IC7300 rigctld):
  `f` returned 433500000; `\\run_module 26` was refused;
  `\\run_module 27` returned `RESULT 8 | RPRT 0` and resulted in
  3/3 CRC-valid packets on the SDR; bridge terminated afterward.

## Reproduction

Build a module **offline**, without touching serial or radio:

```sh
cd /storage5/dev/flux-sotto-api29-irq-fifo-mock-20261125
~/.local/share/flux-rf-venv/bin/python tools/k6_sotto_pcm_rf_build.py \
  --out /tmp/sotto-pcm-rf-current --settle-ms 20 --interframe-ms 0
```

This intentionally creates a self-contained, inert-on-install API31
module. Only a foreground, explicitly initiated module RUN requests
TX. Codebook and deck need to be present as verified typed resources.
No live microphone or PC-to-radio PCM data required.

Bench wrapper: `tools/k6_sotto_rf_goal_bench.py`. It checks exclusive
bench owner `flux-sotto-rf`, radio build and API, serial ownership,
known module image hashes, idle VFO and receiver tuning. Its
temporary slot27 restore is *not optional*. Private evidence lives at
`/storage5/dev/sotto-monitor/evidence/bench-rf-goal-20261125/`, not in
Git (raw IQ, audio diagnostics, modem run returns and original module
envelopes).

Live SDR configuration in this test: physical center 433.750 MHz,
0dB fixed gain, configured signal 433.500 MHz, digital tune offset
-30.5kHz, capture sample rate 1.024MS/s and demod sample rate
128kS/s. With monitor from the source repo:

```sh
SOTTO_RTL_IF_OFFSET_HZ=-250000 \
  ~/.local/share/flux-rf-venv/bin/python app.py
```

All 11 monitor unit tests and 54 focused Flux module/Hamlib/virtual
tests passed in the relevant worktrees. This demonstrates a bounded,
generated-tone RF/codec path rather than a production live-mic
interoperability sign-off.

## Deliberately outstanding

The production API31 host `fsk_async()` primitive still mutes PA for
preload, unlike the qualified continuous-PA direct-register sequence.
The original microphone SOTTO TX user experience has not been fully
requalified using the new RF sequence. Porting the tested PA lifecycle
into an opt-in API31 host session requires an explicit firmware
change/build/flash and independent verification. **Do not silently
declare this production primitive fixed.**

Temporary library slot27 should be restored to original `FSK DIAG`
(code SHA256
`56f2dd94c352352590907f93c8f1eb934fa44cece88eddd9957ce20cf6964bd5`)
and bench released only after all tests and evidence export are complete.
