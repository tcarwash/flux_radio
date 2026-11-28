# 2026-11-25 live-serial K6 Sotto 1 kHz bench session

**Scope:** authorized dummy-load K6 RF development at VFO A
433.500 MHz, connected PL2303 serial cable and air-coupled RTL-SDR.
All temporary module-library changes were restored and verified.
Original FSK DIAG in slot 27; no firmware MCU flash was performed.

## Environment

- Host `tyler-ldesktop`, `/dev/ttyUSB0`.
- Live API capabilities: `module_api_max=31`, `cable_only=True`,
  `coop_cancel=True`, packed KUL2 protocol 5, 14,976-byte module arena.
- Live `lib_mutate` is **not advertised** and all 32 logical KUL2
  slots were occupied; legacy `CABLE_HANDSHAKE` loader was verified
  and used for temporary **slot 27 only**.
- SDR Monitor 433.500 MHz nominal, decoder offset -30.8 kHz. Earlier
  physical session rejected 13 frames; that evidence was archived
  **before** restarting the SDR. This session's monitor was started
  with counters zero and ended with 0 CRC accepted / 0 rejected,
  because no Sotto FSK transmission was successfully keyed.
- Original signed slot-27 `FSK DIAG` envelope SHA-256:
  `522257778a0dd5221611ca597d4ec8751c2620dab2bcc5b8dde34fbe35efc08a`,
  API29, 1329 code bytes. The backup is
  `/storage5/dev/sotto-monitor/evidence/bench-20261125/slot27-original.k6m`.
  Kept private, excluded from Git.

## Real hardware results

| Test | Module / launch | Physical result | SDR observation |
|---|---|---|---|
| Live PTT baseline | `ptt-burst --seconds 0.8` | Returned idle RX, 433.500 MHz | RF power rose approx -31 to +1.3 dB |
| 1 kHz on-radio encoder v1 | `SOTTO TONE` 9956 B, API29, slot 27 | Module returned **-8**, `no RAM`; no TX | 0 Sotto packets |
| Direct PCM encoder v2 | 9496 B, API29, slot 27 | Module returned **-5**, `tx_on(9000)` refused; no TX | 0 Sotto packets |
| Known tone frames v1 | `SOTTO 1K FX` 1344 B, API29 | Module returned **-51**, `tx_on(3000)` returned -1 | 0 Sotto packets |
| Known tone frames v2 | 1340 B, API29 | Module returned **-51**, `tx_on(1200)` returned -1 | 0 Sotto packets |
| Bare TX lease | `TXLEASE PROB` 228 B, **API3** | Module returned **77**, `TXLEASE OK`, 250 ms FM carrier; idle RX | RF power rose approx -32.8 to -1.5 dB |
| Bare TX lease, otherwise identical | Same 228 B probe, **API29** | Module returned **-51**, `TXLEASE refused`; idle RX | No RF, level ~-32.8 to -32.7 dB |

**Critical controlled comparison:** archived API3 and API29
`TXLEASE PROB` binaries are **both 228 bytes, with exactly one
byte difference at module offset 36**: the immediate in the
`a->level < 3` vs `a->level < 29` comparison. Both modules executed
their body (API29 returned -51 instead of the early -3), and both
requested the same `tx_on(1200)`. They were installed/verified via
the same serial loader, launched via the same cooperative runner, at
the same VFO. This is compelling physical evidence of
**API-level-correlated transmitter refusal**, though **not proof of
the underlying cause**. The source implementation of `mod_tx_on`
does not explicitly check the module's declared API level; it may
fail due to `enter_tx()` or state/dispatch differences.
The firmware binary running on the radio must be audited, not
assumed identical to current source. Do not remove TX authorization
or band-lock checks as an untested workaround.

The `load=410124858` field in the 9496-byte module's live result
was anomalous; the two compact fixtures returned plausible
load values. It was not interpreted as successful transmission.

## Offline virtual results

- Native Sotto 1kHz PCM: 320 ms at 8 kHz PCM16, exactly eight
  6-byte codec frames. Public synthetic fixture SHA-256:
  `d0dcef27d3113e2d297c23ba79a873b80a81c9940618f2323ca57c0800a464ad`.
- `SOTTO 1K FX`: **3/3 CRC-valid virtual packets**, complete FDV1
  BEGIN/VOICE/END, eight reference-identical codec frames, all 99
  virtual receive FIFO words, zero FIFO under/overrun. Physical RF
  acceptance remains **NOT TESTED** because TX was refused.
- The compact, 9496-byte `SOTTO TONE` encoder also passes the
  native mock and virtual IQ checks. Physical first-run scratch was
  insufficient with 9956 bytes; the revised 9496-byte version got
  to `tx_on` but was refused.

## Restoration / closeout

After diagnostic runs:

1. Reinstalled original `FSK DIAG` code in slot 27 through the
   legacy authenticated-via-CRC32 cable-only loader; used a newly
   monotonic envelope serial, since the loader refuses replay.
2. Verified fresh radio readback: code bytes match original
   **SHA-256 `56f2dd94c352352590907f93c8f1eb934fa44cece88eddd9957ce20cf6964bd5`**.
3. VFO A manually restored to **433.500 MHz** after loader reset
   (this firmware reboot defaults to 443.500 MHz).
4. Verified `transmitting=false`, `standby_idle=true` at 7.73 V.
5. Saved final SDR export and IQ NPZ; stopped monitor (`state=idle`).
6. Explicitly released bench owner `sotto-tone-live`; confirmed
   **`nobody holds the bench`**.

No original SOTTO TX slot 24/30 changes. No live test left running.
Raw log and JSON files stay in
`/storage5/dev/sotto-monitor/evidence/bench-20261125/`, not Git.

## Next investigation

Before running further physical Sotto RF, inspect the compiled
firmware build `5d8e7f39844d42f9`:
- Why does the exact same minimal 228-byte module permit
  `mod_tx_on()` at API3 but refuse at API29?
- Trace actual `live_queue_module()`, `run_module()`,
  `mod_tx_on()` and `radio.enter_tx()`; distinguish invalid
  module context from RF state/band lock and GPIO PTT/cable ownership.
- Add a bounded, non-RF diagnostic return code for TX refusal
  branches, retaining all existing band/PA/watchdog safeguards.
- Only after the TX lease path is understood should the next
  physical test use the tiny `SOTTO 1K FX` to isolate real
  FIFO/CRC timing from the live codec. Keep existing saved
  rejected IQ and compare after the first successful RF packet.
