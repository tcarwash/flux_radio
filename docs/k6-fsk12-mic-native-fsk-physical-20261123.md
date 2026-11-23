# FSK-12-RF: physical K6 microphone-derived Sotto L64 burst — strict FAIL, exact 0x00F8 displacement

Date: 2026-11-23. **One bounded physical test performed; NO automatic RF retry.**
Research branch `research/live-dv-integration-20261123`.

## Prospectively frozen physical question

Does the K6 successfully transmit the four already captured / host-encoded
microphone-derived Sotto-1200 packets over its finite native 4800-bps FSK
modem, with blind acquisition and CRC16-valid reconstruction?

The original [prospective RF protocol](k6-fsk12-mic-derived-native-fsk-prospective-20261123.md)
and exact hash-pinned 1196-byte module were committed in `a765a18`,
followed by explicit VFO-state preservation in `a1436a2`, **before RF**.

## Strict RF outcome: FAIL / INCONCLUSIVE

A single guarded FD6818B (0x4829) transmission completed firmware-side:
four finite native FSK bursts, each **32 FIFO words / 64 bytes**, for a
reported total of **128** words. The actual module was precommitted as
four frames and returned 128; its inherited LCD report string says
"eight packets sent" but is a stale cosmetic label, not its frame count.
That source was not changed post-RF.

An independent payload-blind SDR receiver with **only universal 16x55
preamble and 076b3e36 sync at fixed 4800 baud**, not provided expected
voice bytes for acquisition, found **exactly four** 64-byte RF frames:

| RF frame | Payload start (s) | Public-sync correlation | Strict bit errors / 512 | CRC16 valid? |
| --- | ---: | ---: | ---: | --- |
| 0 | 0.8122135 | 0.994487 | 252 | No |
| 1 | 0.9636719 | 0.995809 | 248 | No |
| 2 | 1.1147135 | 0.994182 | 250 | No |
| 3 | 1.2662240 | 0.995341 | 248 | No |
| **TOTAL** | — | — | **998 / 2048** | **0 / 4** |

**Do NOT claim a valid on-air Sotto packet, decoded voice, or a successful
microphone-to-RF application transport.** The prospective criteria
were 4/4 CRC valid, 0/2048 data bit errors and complete source
packet identity. They were not met.

## New offline-only forensic diagnosis

A separate post-hoc decoder that **does not replace the prospective
receiver** shows the exact same 16-bit insertion pattern in **every**
native FSK frame:

```text
observed_frame_words = [0x00F8] + intended_packet_words[:31]
```

Each independently received 64-byte frame began with `f8 00`.
The following **62 bytes** match exactly the first 62 bytes of
that frame's real microphone-derived CRC packet. Every single one
of the **2048 received bits** matches the post-hoc leading-control-word
hypothesis (0 bit errors), but **the final two intended bytes are missing
from the declared RF frame**. Those two bytes carry part of the
original packet tail and prevent CRC acceptance. This is NOT a
correction that yields a valid packet: the missing bytes were not
received and must not be silently invented.

The artifact exactly parallels the separately proven
[FSK-09S-G fully preloaded 60-word control](../docs/k6-fsk09s-preload60-physical-failure-20261123.md):
`0x00F8` plus 59 intended words when **no live FIFO appends**
occurred. This makes a simple live-append timing error an inadequate
explanation. The fact that older FSK-10B physically recovered **8/8
valid Sotto packets with no extra word** means the behavior is
not universal; hardware/control state and FSK initialization differences
still need controlled investigation. `0x00F8` is also a `REG_59`
control value written in setup, but it has NOT been proven to be
erroneously misaddressed or directly placed into FIFO by SPI.

## Radio / hardware safety evidence

- Other active Sotto/FIFO agent formally ACKed and yielded one FSK-12
  RF transaction to this branch, and agreed not to transmit its
  independent, duplicate prepared fixture. Additional modulation
  research agent previously ACKed no hardware lease. The shared #11
  issue received LEASE START before physical operation; fresh host
  process/fuser scan found no conflicting serial/SDR use.
- Dummy-loaded K6 lab at 433.500MHz, RTL-SDR 433.750MHz/1.024Msps,
  single module invocation, **1900ms independent TX watchdog**.
- `serial_lock('/dev/ttyUSB0')` acquired nonblocking and held for
  radio preparation, capture, RF, signed original module reinstall
  and full VFO verification. No other worktree modified.
- Frozen **private audio packet file** SHA256
  `d81956bf1ae57747eaf8353edd2d10453b530408a61e781600ee8c7dc05d8bd6`.
  All four independent per-packet SHA256 values matched the other
  agent's hash-only manifest **before RF**. Actual captured microphone
  PCM, Sotto bitstream, 256 source packet bytes and private generated
  module source are **NOT in Git**.
- Frozen armed C source SHA256
  `b1cf48083d990a853054a4ec36797d5c5ffbc53e0a7c9b690193d4d66131dd56`.
  Compiled API3 code **1196 bytes**, SHA256
  `46a7c785297f91428b09496bf251ca96fe744cf6794464e0ca23503020ffd77a`,
  exact preflight hashes verified immediately before hardware.
- The original signed slot30 code fingerprint was backed up,
  restored and verified by readback; both operator frequencies
  **A=440.850 MHz (watched), B=144.390 MHz**, FM/wide, are
  unchanged after transaction. Final TX=False, standby_idle=True,
  battery about 8.08V. No MCU firmware flash or SPI calibration erase.
- Air-coupled SDR is not a calibrated spectrum analyzer.

## Reproducible private evidence

Directory (not uploaded to Git):
`/home/tyler/flux-bench/tx-injection-20261122/fsk12-mic/`

- `fsk12-mic-run01.npy` original RTL-SDR I/Q SHA256:
  `cba7245ea7cd04f21fd5857a4d68ccbe3df9695039d8bfb6ea99a1c9e030c3d0`.
- `fsk12-mic-run01.json` guarded transaction, all module
  attestation and restoration evidence.
- `fsk12-mic-analysis-run01.json` frozen prospective
  public-sync receiver **FAIL** and four failed CRCs.
- `fsk12-mic-forensic-run01.json` explicitly post-hoc
  `0x00F8`/tail displacement diagnosis without raw private audio.
- `fsk12-mic-run01-slot30-before.k6m` original signed
  module envelope backup.

Run offline:
```sh
python3 tools/k6_fsk12_mic_rf_forensic.py \
  --iq /home/tyler/flux-bench/tx-injection-20261122/fsk12-mic/fsk12-mic-run01.npy \
  --packets /home/tyler/flux-bench/micvoice-20261123/sotto-offline-run01/k6mic-l64-packets.bin \
  --out /tmp/fsk12-mic-posthoc-forensic-recheck.json
```

## Next gate (NO hardware lease held after this run)

1. Consolidate G and FSK-12 evidence in issue #15:
   the leading `0x00F8` is possible with **all FIFO data preloaded**
   and appears before every real Sotto packet in this trial.
2. Audit original physically successful 8-packet FSK-10B TX
   source/REG_59 and SPI sequence against FSK-12 failing source,
   exact compile flags, standby state and prior module/ROM state;
   distinguish a programming sequence difference from inter-run
   hardware state before designing a newly frozen RF discriminator.
3. Avoid blind repeated TX, speculative FIFO reads, infinite streams
   or reclassification of 0/4 CRC as success.
4. Separate parallel next objective: on-radio Sotto slim encoder /
   packet scheduler / speaker integration with measured M0 RAM and
   CPU budgets. The existing K6 mic capture and host codec succeed
   offline; a standalone two-way DV call is NOT yet established.
