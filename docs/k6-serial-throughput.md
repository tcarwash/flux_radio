# Serial throughput experiments

Flux starts at 115200 baud. The normal installer and catalog continue to use that
rate. These tests add a **session-only, opt-in** serial lab for measuring a physical
radio and cable, without transmitting RF or changing SPI contents. Restarting the
radio always restores 115200; five seconds of idle in a cable session also resets it.

Build an isolated Data + Scan image with the test protocol:

```sh
K6_SERIAL_LAB=1 K6_FSK_RX_SERVICE=1 K6_SCAN_SERVICE=1 \
  sh firmware/k6-aura/build-fd6818b.sh
```

Use a separate checkout/build directory when another firmware build is in progress.
Take a complete SPI backup and keep a matching rollback firmware image before
installing experimental firmware. The lab commands are absent unless
`K6_SERIAL_LAB=1` is set. Normal builds still compile without the lab.

## Why output and input need separate tests

On the tested Prolific PL2303 programming cable, the two directions have different
limits. The radio reported noise/framing errors on sustained PC-to-radio traffic
above 192000 baud, without hardware overruns or software ring drops. That points
to a physical receive-path limitation in this setup, rather than buffer exhaustion.
It does not establish the limit of every cable or radio.

A burst avoids requiring fast commands: send `baud FAST`, `stream`, `baud 115200`
as three frames at 115200. All three fit in the RX ring. The radio acknowledges the
first command at the old rate, waits 30 ms, streams at FAST, acknowledges the last
command at FAST, waits 30 ms, and restores 115200. The host drains its transmitted
commands before changing its baud, and follows both ACKs. Commands remain at the
baseline rate throughout. RF stays off for the entire cable session.

## Hardware measurements

Measured on the connected FD6818B Flux radio and Prolific PL2303 cable:

| Test | Requested baud | Payload rate | Evidence |
|---|---:|---:|---|
| Original 64-byte pipelined SPI reads | 115200 | 8.3 kB/s | Repeated 64 KiB reads matched |
| Full bidirectional suite | 192000 | 12.4 kB/s SPI; 9.5 kB/s echoed payload per direction | No CRC, noise, framing, overrun or ring-drop errors |
| Fast pattern bursts | 500000 | 42.4 kB/s | Eight 256 KiB bursts (2 MiB) verified; zero UART errors |
| Fast SPI bursts | 500000 | 40.0 kB/s | Four 64 KiB reads matched the original backup |
| Fast pattern bursts | 550000 | 46.3 kB/s | Eight 256 KiB bursts (2 MiB) verified; zero UART errors |
| Fast SPI bursts | 550000 | 43.4 kB/s | Four 64 KiB reads matched the original backup |

550000 is the fastest **tested clean** output setting, with an actual MCU baud of
548571 after divisor rounding. 560000 lost frames; 570000, 576000, 600000 and the
higher settings tested through 3000000 failed validation. Use 500000 for more margin.
This is evidence for this particular cable/radio/host, not a universal UART limit.

Direct PC-to-radio traffic at 230400 passed short individual echoes but failed the
pipelined echo test with noise flags. At 256000 it also produced framing errors.
192000 passed the complete bidirectional test with all four UART counters at zero.
The fast burst mode keeps PC-to-radio commands at 115200 instead.

## Reproduce

Full bidirectional tests include pattern streaming, arbitrary binary echoes, and
ordinary SPI reads. Tests disable retransmission so errors cannot be hidden:

```sh
python3 tools/k6_serial_bench.py --port /dev/ttyUSB0 \
  --rates 115200 153600 192000 230400 \
  --output serial-test-output/duplex.json
```

Repeated fast output with control at 115200:

```sh
python3 tools/k6_serial_bench.py --port /dev/ttyUSB0 \
  --downlink-only --rates 500000 --blocks 1024 --passes 8 \
  --output serial-test-output/stream.json
```

Real SPI reads, compared against a complete 2 MiB backup:

```sh
python3 tools/k6_serial_bench.py --port /dev/ttyUSB0 \
  --spi-reference /path/to/backup.bin --rates 500000 --blocks 256 --passes 4 \
  --output serial-test-output/spi.json
```

`--blocks` is the number of 256-byte output frames (1–1024). SPI bursts start at
physical address `0x100000`. No benchmark writes the SPI flash, installs modules,
or transmits RF. Installing the lab firmware itself replaces MCU firmware.

## Wire protocol

All commands use the existing A6 / command / big-endian address / big-endian length /
payload / CRC-16-XMODEM framing. CRC initial value is zero. Output frames may have
256 data bytes; inbound frames remain limited to 96 bytes.

| Command | Request | Response |
|---|---|---|
| `0x62` (`b`) | Address 0, desired baud as u32 LE, 115200–3000000 | Actual rounded baud as u32 LE at old rate; 30 ms guard then switch |
| `0x54` (`T`) | Up to 96 arbitrary bytes | Exact echo at the same address |
| `0x74` (`t`) | Starting sequence in address, frame count as u16 LE (1–1024) | Count frames, 256-byte deterministic pattern each, sequential addresses |
| `0x64` (`d`) | Physical start address / 64, count as u16 LE (1–1024) | Count 256-byte SPI frames; addresses advance by 4; reads bounded to the 2 MiB chip |
| `0x71` (`q`) | Empty | Four u32 LE counters: hardware overrun, framing error, noise flag, software ring drop |

Pattern byte `i` in sequence `s` is `((i * 73) & 255) ^ (s & 255) ^ (s >> 8)`.
Each frame has its own CRC and sequence check. Bursts are bounded to 256 KiB.
Counters reset at boot. A lost rate switch is recoverable by waiting for idle reset
with no further bytes sent, then reopening at 115200.

The lab uses a nibble-table CRC implementation verified against the bitwise version.
The host uses `binascii.crc_hqx` so Python CRC processing does not dominate measurements.
Response encoding computes CRC directly over the encoded frame, avoiding an inbound-
size scratch buffer and a second payload copy.

## Interpretation

Payload rates count application bytes, excluding frame overhead; echo rates count
one direction, although the same payload also travels in the other direction.
Burst timing includes the first baud handshake and guard, so small bursts have more
overhead. The baud itself is a bit rate: 8N1 takes ten line bits per byte.

These are cable transfer measurements. They do not yet prove uninterrupted ADC/IQ
capture, DMA operation, or simultaneous RF decoding at those rates. Continuous
sample streaming needs its own overruns, timing and RF tests.
