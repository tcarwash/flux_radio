# Flux live serial control (experimental)

The normal loader is deliberately non-returning: it changes stack ownership,
stops RF, and resets after END or timeout. Live v1 is separate and runs one
bounded request per host-loop pass, using the existing returning UI arena
stack trampoline. No loader handshake, END, explicit reboot, UART reconfiguration,
or SPI write occurs in the new path. The legacy protocol and bootloader capture
remain unchanged.

## Build and use

The feature is opt-in during hardware validation:

    K6_LIVE_SERIAL=1 sh firmware/k6-aura/build-fd6818b.sh

Flash the resulting firmware with the normal installer when the radio is
available; do not interrupt another bench job. In normal VFO standby, use:

    python3 tools/k6_live.py --port /dev/ttyUSB0 status
    python3 tools/k6_live.py --port /dev/ttyUSB0 tune 146520000
    python3 tools/k6_live.py --port /dev/ttyUSB0 watch --interval 0.5

Use the actual K6 port (not an attached rig's port). The Catalog's My Radio
panel also exposes status and VFO tuning. Memories uses the live API by
default; its explicitly selected legacy checkbox restores the older rebooting
read path. No fallback is automatic, including on timeout. Older firmware
will not respond to live requests. The Catalog memory programming and recovery retain the loader; the live bank read preserves native decoding and pending-write identity. The Catalog library inventory and module
install/remove still use the old loader.

## Wire protocol

115200 baud; no handshake or persistent session. Each request is exactly 14
bytes: ASCII FLXLV1 (6), opcode (1), sequence (1), uint32 LE argument (4),
CRC16/XMODEM over the preceding 12 bytes (2, BE). The existing rolling UART
handshake detector recognizes the prefix and valid CRC, so legacy handshake
strings continue to work independently. The host sends one request at a time.
Responses use the existing CPS envelope: A6, opcode (1), sequence zero-extended
to 16 bits BE, length BE16, payload, CRC16/XMODEM BE excluding A6. First payload
byte is result: 0 success, 1 busy, 2 invalid, 3 SPI I/O error.

| Opcode | Argument | Success payload after result byte |
|---|---|---|
| 60 hex | 0 | FLIV, version 1, master/watch indices, flags, A/B Hz LE32, battery centivolts LE16, cached RSSI dBm signed LE32, A/B modulation IDs, A/B wide flags |
| 61 hex | 64-byte aligned physical SPI address 0..0xb000 or 0xf000..0xffc0 inclusive (calibration identity for recovery) | 64 bytes of checked SPI read |
| 62 hex | integer Hz | empty; updated state can be queried with 60 |

Flags: bit 0 TX, 1 squelch open, 2 standby/no PTT/CW TX, 3 power-save asleep,
4 A memory mode, 5 B memory mode. Modulations: FM=0, AM=1, USB=2, CW=3, CWF=4.
Status reflects cached UI RSSI and battery state, not an instantaneous ADC read.

Tuning intentionally changes only the active VFO in RAM, synchronizes the
watching side using the normal radio configuration path, and preserves its
existing offset, modulation and power. It requires standby, awake RX, no PTT,
VFO (not channel), no reverse, and no selected resident digital engine.
The initial conservative test range is 136–174 MHz or 400–480 MHz at 10 Hz
resolution; this is not a statement about RF capability or TX legality.
No remote PTT, write-memory, raw register, or reset command exists. Later local
VFO saves may persist the tuned frequency; a live tune does not itself save.

The controller uses the existing process port lock. A full memory read is
currently sequential (one 64-byte response per main-loop pass), and can fail
with a busy error if the local operator changes modes during the read; it
never switches to the disruptive loader on its own.

## Validation and next steps

- Run host tests: `python3 -m pytest -q tests/test_k6_live_control.py tests/test_k6_memories.py`.
- Compiled-image simulator: `K6_SIM_LIVE_IMAGE=/path/to/image.bin python3 -m pytest -q tests/test_k6_live_control_sim.py`.
- On hardware: use a dummy load or isolated RX bench, verify no boot banner and
  no unintended PTT during repeated status/reads, compare actual RF frequency
  to VFO display after tune, verify keypad interaction and unchanged stored
  memory, measure response latency and RX audio gaps while polling. Check
  sleep, open squelch, busy/TX, reverse, memory and resident-engine guards.
  Exercise disconnect/reconnect and legacy update bootloader entry separately.
- Then consider an opt-in rigctld-compatible local bridge, explicit mode
  control, active VFO selection, and channel selection. Remote PTT needs an
  independent firmware lease/timeout and physical interlocks before enabling.
  A live full SPI backup or library inventory needs an explicit expanded read
  map and throughput test.

Emulator and host results do not prove physical RF, cable/PTT electrical
behavior, timing, or receive continuity.
