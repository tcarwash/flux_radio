# Sotto live API29 33-word FIFO: offline tail-timing differential

**2026-11-24 · model + saved IQ only · NO radio, SDR hardware, serial,
bench lock, flashing, mic or transmission.**

## Why this matters

The active live Sotto TX sends a 66-byte physical frame (33 words), where
bytes 0..63 contain a CRC16/CCITT-FALSE + PN9-whitened L64 packet, and
bytes 64..65 contain the sacrificial `0xA55A` final word in little-endian
on-air order (`5A A5`). The public 16×`55` + four-byte sync is transmitted
before the physical data. At 4800 bits/s, 66 physical bytes require
**110 ms after sync**, not including preamble/sync.

The independent receiver acquired recognizably valid `0xA9` Sotto voice
and `0xE2` FDV1 BEGIN headers but found **zero CRC-valid packets** across
the latest 13 candidates. Twelve IQ snippets were saved privately under
`/storage5/dev/sotto-monitor/evidence/`. A quantitatively verified
discriminator report is in `sotto-monitor/docs/physical-iq-burst-tail-2026-11-24.md`:
- 9/12 show modulation loss **by bytes 54..59**, followed by IQ fade.
- 2/12 modulate until near the tail, then lose power.
- 1/12 retains FSK and power through the 66-byte window, yet fails CRC.

These observations DO NOT by themselves establish PA/FIFO origin; SDR receiver
buffer/radio mode changes, clipping or synchronization errors remain possible.

**Important prior physical control:** FSK-14 (Flux issue #18) recovered
**4/4** 33-word guarded L64 packets with **the same `REG_5D=0x4100`**.
Therefore the register constant cannot simply be declared incorrect for
every configuration; a fixed 32-word TX FIFO capacity is contradicted by
that successful previous operation unless an additional conditional fault
is involved. See
`/storage5/dev/flux-live-dv-fifo-rootcause/docs/k6-fsk14-real-microphone-guard-prospective-20261123.md`.

## Source-anchored fault model

`tools/k6_sotto_tail_timing.py` makes read-only SHA256 fingerprints of
`firmware/k6-aura/sotto-api29-async-fsk.rs.inc` and
`c/k6/mod/sotto_live_session.h`. It **rejects source changes** that
invalidate the modeled sequence (33 preloaded words → PA-on → FSK enable,
`0x5D=0x4100`, FIFO completion via `0x0C`, native guard).
It constructs a public deterministic Sotto-1200 L64 packet with 48 bytes
of synthetic 6-byte codec slots, seals it using the production Python
CRC/whitening profile, appends the `5A A5` guard and hashes the resulting
66 bytes. No private recording or voice content is used.

Its hypothetical events start at the **first physical payload bit after
sync**. Simulated changes are:
- `nominal`: TX-done/cleanup occurs at 110 ms, after all 66 bytes.
- `early_done`: the RFIC IRQ is assumed asserted at 90 or 100 ms.
  The module's polling cadence causes cleanup at the next poll.
- `early_pa_drop`: independent simulated PA/TX-ready loss at 90 ms
  terminates modulation regardless of when the IRQ is handled.
- `fifo_limit`: an artificial 32-word capacity stops after 64 bytes,
  generally *after* the packet CRC but *before* the sacrificial guard.
  This is **a negative control**, not an established hardware property.

The model does not emulate actual silicon, module CPU timing, SPI and
physical PA release; the interrupt meaning and trigger timing are unknown.
It is a falsifiable **causal hypothesis harness**.

### Measured model outputs

| Injected scenario | Hypothetical first missing byte | L64 CRC transmitted | Guard transmitted |
| --- | ---: | :---: | :---: |
| Complete frame, IRQ at 110 ms | No missing byte | Yes | Yes |
| Early TX-done at 90 ms, 5-ms polling | 54 | No | No |
| Early TX-done at 90 ms, 7-ms / phase-3 polling | 56 | No | No |
| Early TX-done at 100 ms, 7-ms / phase-3 polling | 60 | No | No |
| TX-ready/PA loss at 90 ms | 54 | No | No |
| Artificial 32-word FIFO | 64 | Yes | No |

The same model optionally emits **labeled synthetic complex IQ**
(`--scenario early_done --iq /tmp/example.npz`). An independent
Sotto Monitor decoder accepted the complete synthetic packet
(1 CRC-valid frame at ~0.9998 public sync correlation). When
early TX-done was injected into the same signal, it retained
~0.9998 sync correlation but delivered zero CRC-valid packets.
The monitor's tail-loss classifier identified the synthetic early
cutoff correctly. **This is an offline reproduction, not proof of
the physical cause.**

## Reproduce

```sh
cd /storage5/dev/flux-sotto-tail-offline-20261124
~/.local/share/flux-rf-venv/bin/python -m pytest -q \
    tests/test_k6_sotto_tail_timing.py \
    tests/test_k6_sotto_live_packet_parity.py \
    tests/test_k6_sotto_api29_overlay.py

~/.local/share/flux-rf-venv/bin/python tools/k6_sotto_tail_timing.py \
    --out /tmp/k6-sotto-tail-timing.json
~/.local/share/flux-rf-venv/bin/python tools/k6_sotto_tail_timing.py \
    --scenario early_done --iq /tmp/sotto-synthetic-early-done.npz
```

The test suite fails on changed source contracts; tests assert exact
canonical byte recovery, virtual FIFO/IRQ fault timing, late-frame cutoff,
and that a purely hypothetical post-IRQ hold could distinguish an IRQ-driven
cleanup from an independent TX-ready/PA loss. **Do not implement a blind PA
hold delay on actual hardware**: preserve watchdog, power-off and emergency
abort semantics.

## Microphone-free hardware qualification queued

[Flux SOTTO-TAIL-01 / issue #39](https://github.com/tcarwash/flux_radio/issues/39):
after an independent radio/SDR lease and dummy-load/safety checks, compare
an identical *known synthetic packet* through the previously successful
guarded-FSK path and the live API29 FSK path, capturing RF symbol count,
real TX-done status and TX/PA cleanup timing. Only accept uniquely
CRC-validated full packets, exact guard, and complete state restoration.

This new test is separate from [FDV1 physical session experiment
#30](https://github.com/tcarwash/flux_radio/issues/30), which is also
microphone-free. Do not infer hardware execution permission from a
GitHub issue alone. The bench owner check was **nobody** at the start
of this offline work. No remote transmission attempted.
