# Flux FIFO `0x00F8` — passing FSK-10B vs failing FSK-12 register-sequence audit

Date: 2026-11-23. **OFFLINE source-only comparison**; no transmitter/SDR/serial operations. Other agent completed a guarded FSK-12 real-K6-mic RF experiment, with 0/4 CRC-valid packets and a new `0x00F8` *first word* inserted into each of four finite native-FSK frames; all four original voice packets were CRC-valid before transmission. See [strict FSK-12 physical failure](https://github.com/tcarwash/flux_radio/blob/research/live-dv-integration-20261123/docs/k6-fsk12-mic-native-fsk-physical-20261123.md) and [FIFO root-cause issue #15](https://github.com/tcarwash/flux_radio/issues/15). All operator hardware and signed original slot30 / VFOs were verified restored, lock released. We **must not** call this RF voice delivery a success: missing final two bytes render each received L64 CRC invalid.

## Controlled comparison

The earlier physically proven FSK-10B used `c/k6/mod/fsk_sotto_l8_burst_armed.c` and **successfully sent eight 64-byte CRC-valid Sotto packets**, independent SDR receiver passing 8/8. The other agent's **failed** FSK-12 K6-microphone-derived trial used private `fsk12_k6mic_private_armed.c` in the local bench, with four 64-byte voice packets from the actual radio microphone. The private microphone audio and the C payload arrays were **never printed, logged, or placed in Git** during this audit.

Read-only `tools/k6_fsk_tx_source_parity.py` parsed the two source files and compared their **FSK frequency/gain/baud/word/timing macros**, every `W(REG,value)` control write, FIFO write call, and TX safety/PA/status operations, *without inspecting any array contents for output*. Results:

| Audit component | Result |
| --- | --- |
| FSK frequency, gain, baud, words/frame, 1900ms TX lease, 700ms frame timeout macros | **Exactly equal** |
| 17 register write call sites, including 0x00F8 clear, 0x80F8 FIFO reset, 0x08F8 start, REG5F writes | **Exactly equal and in same source order** |
| PA/TX-on/TX-off calls, frame-completion poll, power/key guards | **Exactly equal** |
| Frame count | **8 (passed) vs 4 (failed)** |
| Embedded compressed speech / C static payload words | **Different and private; intentionally excluded** |
| Compiled module code layout/instruction timing | **Different; not yet tested for equivalence** |

The explicit `send_frame` instruction sequence used by both is: stop/control 0x00F8 → clear IRQ → set 64-byte frame length → clear FIFO with 0x80F8 → restore control 0x00F8 → write 32 words via REG5F → start 0x08F8 → poll completion. The initial TX setup also uses the same work mode, FSK gain, sync, baud and PA path.

**This strongly constrains—but does not solve—the anomaly.** A simple source-level change in RF register sequence between the successful and failing packet transmitters is **not** present. Possible causes include RFIC state surviving cleanup/power transitions, timing differences due to private image code layout/word values, `fd_write` low-level SPI address/latch behavior, or a silicon FIFO-clear/start boundary condition. The fact that the FSK-09S-G **fully preloaded** 60-word control failed with the same leading word independently eliminates any requirement for a mid-frame FIFO append to trigger it. `0x00F8` is a control value written to REG59, but *how* it became the first RF payload word is **unproven**.

No speculative attempt to remove the first word, extend hardware frame length by 1, trim CRC, read destructive FIFO, change native FSK register order or run a 'quick' hardware repeat was performed. A future physical discriminator must have a separate prospective source/protocol and a new explicit issue #11 bench lease and safety approvals.

### Reproduce read-only

```bash
python3 tools/k6_fsk_tx_source_parity.py \
  --out /tmp/fsk10b-fsk12-register-sequence.json
python3 -m pytest -q tests/test_k6_fsk_tx_source_parity.py
```

Private local JSON includes **only SHA digests, count and boolean matches**, no actual voice/packet contents. Current hypothesis ranking: inspect **radio state + low-level SPI/RFIC register write implementation** and compile timing next; do not presume that apparent control-word insertion is a software cross-address write without a trace.
