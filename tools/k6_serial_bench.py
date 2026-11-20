#!/usr/bin/env python3
"""Measure an opt-in K6_SERIAL_LAB firmware over its programming cable (no RF).

Every session opens at 115200. The radio resets to 115200 on end/idle.
No failed test is retried: errors cannot be hidden by retransmission.
"""
import argparse
import binascii
import json
from pathlib import Path
import struct
import time
import serial
from k6_install import Cable, CABLE_HANDSHAKE, CMD_INFO, CMD_END, InstallError, cps_frame
from k6_port_lock import serial_lock

RATES = (115200, 230400, 460800, 921600, 1000000, 1500000, 2000000, 3000000)
CMD_BAUD, CMD_ECHO, CMD_STREAM = 0x62, 0x54, 0x74


class BenchCable(Cable):
    # Benchmark explicit rates, never negotiate a second rate inside a read.
    _fast_rate = 0

    def calls(self, reqs, window=1, retries=0):
        out = super().calls(reqs, window=window, retries=retries)
        if self.bad_response_crcs:
            raise InstallError(f'benchmark received {self.bad_response_crcs} response CRC error(s)')
        return out


def pattern(seq, size=256):
    return bytes(((i * 73) & 255) ^ (seq & 255) ^ ((seq >> 8) & 255) for i in range(size))


def once(c, cmd, addr=0, data=b''):
    return c.calls([(cmd, addr, data)], retries=0)[0]


def stream(c, blocks, *, send=True, started=None):
    c.bad_response_crcs = 0
    start = time.monotonic() if started is None else started
    if send:
        c.port.write(cps_frame(CMD_STREAM, 0, struct.pack('<H', blocks)))
    for seq in range(blocks):
        f = c._frame(time.monotonic() + 2)
        if f != (CMD_STREAM, seq, pattern(seq)):
            raise RuntimeError(f'stream mismatch or timeout at block {seq}: ' + repr(f)[:100])
    elapsed = time.monotonic() - start
    if c.bad_response_crcs:
        raise RuntimeError(f'{c.bad_response_crcs} stream CRC failures')
    return metric(blocks * 256, elapsed)


def metric(n, seconds):
    return dict(bytes=n, seconds=round(seconds, 6), payload_Bps=round(n / seconds, 1))


def echo(c, count, window):
    reqs = [(CMD_ECHO, seq, pattern(seq, 96)) for seq in range(count)]
    start = time.monotonic()
    replies = c.calls(reqs, window=window, retries=0)
    elapsed = time.monotonic() - start
    if replies != [r[2] for r in reqs] or c.bad_response_crcs:
        raise RuntimeError('echo mismatch or CRC error')
    # Counts payload delivered in ONE direction. Both directions carry that many bytes.
    return metric(count * 96, elapsed)


def burst(c, rate, blocks, reference=None):
    """Queue at baseline, stream fast, then return to baseline without a fast uplink.

    All three commands fit in the radio RX ring before the first baud switch.
    The final baud ACK goes out at the fast rate; its guard allows host recovery.
    """
    cmd = CMD_STREAM if reference is None else 0x64
    addr = 0 if reference is None else 0x100000 // 64
    start = time.monotonic()
    actual = c.start_downlink(cmd,addr,struct.pack('<H',blocks),rate)
    if reference is None:
        result = stream(c, blocks, send=False, started=start)
    else:
        for seq in range(blocks):
            f = c._frame(time.monotonic() + 2)
            want = reference[0x100000 + seq * 256:0x100000 + (seq + 1) * 256]
            if f != (cmd, addr + seq * 4, want):
                raise RuntimeError(f'SPI burst mismatch at block {seq}')
        result = metric(blocks * 256, time.monotonic() - start)
    c.finish_downlink(rate)
    return actual, result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--port', required=True)
    p.add_argument('--rates', type=int, nargs='+', default=RATES)
    p.add_argument('--blocks', type=int, default=256, help='256-byte stream frames per pass')
    p.add_argument('--passes', type=int, default=2)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--downlink-only', action='store_true', help='queue a stream before switching baud; isolates radio-to-PC limits')
    p.add_argument('--spi-reference', type=Path, help='compare a fast SPI read against this complete 2 MiB backup; implies --downlink-only')
    a = p.parse_args()
    spi_reference = a.spi_reference.read_bytes() if a.spi_reference else None
    if spi_reference is not None:
        a.downlink_only = True
        if len(spi_reference) != 2097152:
            p.error('--spi-reference must be a complete 2 MiB backup')
    if not 1 <= a.blocks <= 1024 or not 1 <= a.passes <= 100 or any(not 115200 <= r <= 3000000 for r in a.rates):
        p.error('unsupported rates or test size')
    a.output.parent.mkdir(parents=True, exist_ok=True)
    results = []
    reference = None
    with serial_lock(a.port):
        for rate in a.rates:
            result = dict(baud=rate, passed=False)
            healthy = False
            with serial.Serial(a.port, 115200, timeout=.002, write_timeout=2, exclusive=True) as port:
                c = BenchCable(port, log=lambda s: print(s, flush=True) if s.startswith('  K6: K6 ') else None, timeout=1)
                try:
                    if not c.handshake(CABLE_HANDSHAKE, wait=4):
                        raise RuntimeError('no handshake at baseline; radio needs normal Flux mode')
                    if a.downlink_only:
                        result['mode'] = 'spi-burst' if spi_reference is not None else 'downlink-only'
                        result['stream'] = []
                        result['rx_before'] = list(struct.unpack('<4I', once(c, 0x71)))
                        for _ in range(a.passes):
                            actual, measurement = burst(c, rate, a.blocks, spi_reference)
                            result['actual_baud'] = actual
                            result['stream'].append(measurement)
                        result['rx_after'] = list(struct.unpack('<4I', once(c, 0x71)))
                        if result['rx_after'] != result['rx_before']:
                            raise RuntimeError('UART errors during baseline control / fast output burst')
                        healthy = True
                        result['passed'] = True
                    elif rate != 115200:
                        reply = once(c, CMD_BAUD, data=struct.pack('<I', rate))
                        if len(reply) != 4:
                            raise RuntimeError('malformed baud reply')
                        result['actual_baud'] = struct.unpack('<I', reply)[0]
                        # The ACK is fully received; change host speed within the radio's guard.
                        port.baudrate = rate
                        time.sleep(.08)
                    else:
                        result['actual_baud'] = 96000000 // round(96000000 / rate)
                    if not a.downlink_only:
                        once(c, CMD_INFO)
                        result['rx_before'] = list(struct.unpack('<4I', once(c, 0x71)))
                        result['stream'] = [stream(c, a.blocks) for _ in range(a.passes)]
                        result['echo_window1'] = echo(c, 128, 1)
                        result['echo_window2'] = echo(c, 512, 2)
                        start = time.monotonic()
                        data = c.read(0x100000, 16384, window=2)
                        result['spi_read'] = metric(len(data), time.monotonic() - start)
                        result['spi_crc32'] = f'{binascii.crc32(data):08x}'
                        if reference is None:
                            reference = data
                        if data != reference or c.bad_response_crcs:
                            raise RuntimeError('SPI read differs from baseline or has CRC errors')
                        result['rx_after'] = list(struct.unpack('<4I', once(c, 0x71)))
                        if result['rx_after'] != result['rx_before']:
                            raise RuntimeError('radio UART error counters increased')
                        healthy = True
                        result['passed'] = True
                except (OSError, ValueError, RuntimeError, InstallError) as exc:
                    result['error'] = str(exc)
                    try:
                        result['rx_after_failure'] = list(struct.unpack('<4I', once(c, 0x71)))
                    except (OSError, ValueError, RuntimeError, InstallError):
                        pass
                finally:
                    if healthy:
                        try:
                            once(c, CMD_END)
                        except (OSError, RuntimeError, InstallError):
                            pass
            results.append(result)
            a.output.write_text(json.dumps(results, indent=2) + '\n')
            print(json.dumps(result), flush=True)
            # No bytes during recovery: inactivity resets even if the baud change failed.
            recovery = 1 if healthy else 7
            if a.downlink_only and not result['passed']:
                recovery = max(10, a.blocks * 2640 / rate * 1.5 + 6)
            time.sleep(recovery)
    return 0 if all(r['passed'] for r in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
