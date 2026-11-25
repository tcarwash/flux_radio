# Native API29 FSK callback: register/FIFO fault injections

Date: 2026-11-25. **Offline only.** No bench, radio, SDR, GPIO,
serial, PTT, microphone, hardware RF, flash or deployment was used.

## Implementation

`tests/data/k6/sotto_api29_native_host.rs` compiles the **unchanged**
production source through:

```rust
include!("../../../firmware/k6-aura/sotto-api29-async-fsk.rs.inc");
```

with `--cfg k6_sotto_async_fsk`. The host defines a minimal mock `App`,
`Radio`, `Fd`, `MOD_CTX`, TX API calls, scratch RAM and 10-ticks/ms
`mod_tick()` clock. It injects `0x0C` TX-done and optional `0x30`
TX-ready loss. All FIFO reads/writes, PA and cleanup actions are logged in
memory. The tests assert actual code's FIFO-write → PA-on → 0x59 TX-enable
order, 33 FIFO writes, completion codes, PA-off before TX-register cleanup,
and restoration of the saved registers. The callback is not a behavioral
reimplementation.

`tests/test_k6_sotto_api29_native_host.py` builds the Rust tests with
`rustc` and supplies a known canonical 66-byte frame from the repo's
Sotto-1200 L64 CRC/PN9 builder: 48 deterministic payload bytes, 64-byte
sealed L64 packet, and little-endian `0xA55A` guard (`5A A5`).

**Timing convention:** FSK-enable to payload byte 0 includes the *public*
160-bit preamble plus 4-byte sync (33.33 ms at 4800 baud). Injected
IRQ/ready-drop times are relative to payload byte 0. The mock clock
is quantized in whole milliseconds for periodic polling and 0.1-ms
ticks internally. A full air frame occupies about 143.33 ms starting
at FSK-enable: 33.33 ms preamble+sync followed by 110 ms for 66 bytes.

## Verified model results

| Fault injected (hypothetical) | Actual API29 return | Carrier-off after FSK enable | Model transmitted payload | CRC / guard |
|---|---:|---:|---:|---|
| Normal 33 words; TX-done after complete frame | `1` success | 145 ms (first poll after 143.33 ms) | 66 bytes | Both delivered |
| TX-done 90 ms into payload; 5-ms polling | `1` **success** | 125 ms | 54 whole bytes + 7 bits | Both incomplete |
| Same early IRQ; 7-ms polling phase 3 ms | `1` **success** | 129 ms | 57 whole bytes + 2 bits | Both incomplete |
| FIFO silently limited to 32 words | `1` **success** | 145 ms | 64 bytes | CRC complete; guard absent |
| TX-ready drops at 95 ms into payload | `-2` abort | 130 ms | 57 whole bytes + 7 bits | Both incomplete |
| Explicit abort at 70 ms after FSK enable | `0` abort API | 70 ms | Not a completed frame | PA and TX lease released |

**These results are conditional on the injected IRQ semantics.** The mock
does not establish that the FD6818B asserts `0x0C & 1` before its final
modulation bit, that actual FIFO capacity is 32, or that the physical
PA has the modeled decay. It also cannot determine whether the RTL-SDR
front end introduced the IQ truncation.

Importantly, an earlier FSK-14 physical control reportedly decoded 4/4
33-word frames with the same `0x5D=0x4100`; therefore a **fixed**
32-word hardware capacity is disfavored by independent evidence. The
most useful remaining measurement is the elapsed time between
FSK-enable, first TX-done status, and real RF tail in a known-payload
burst, together with whether `op=1` returned `1` or `-2`.

## Reproduce locally

```sh
cd /storage5/dev/flux-sotto-api29-irq-fifo-mock-20261125
~/.local/share/flux-rf-venv/bin/python -m pytest -q \
    tests/test_k6_sotto_api29_native_host.py \
    tests/test_k6_sotto_tail_timing.py \
    tests/test_k6_sotto_live_packet_parity.py \
    tests/test_k6_sotto_api29_overlay.py
```

The production API29 firmware and radio were **not changed**. This is
a diagnostic test branch, not hardware-qualified firmware, and nothing
is authorized to transmit from these tests.
