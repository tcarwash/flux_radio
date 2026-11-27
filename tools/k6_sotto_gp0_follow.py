#!/usr/bin/env python3
"""Passive GP0 UART observer, optionally collecting GPF1/GPT1 after module exit.

The installed Flux K6 emits ``K6 MOD run serial=N ...`` and
``K6 MOD ret=X ...`` on the 115200-baud console. A normal CPS/PROGRAM
handshake *during* a synchronous module run cannot service live requests.
This tool never sends a byte until the specific expected module has RETURNED.

The optional readout enters a cable session only after return, preserves both
raw boot-local reports before exiting that session, then reboots the radio.
No RF, flashing, SPI write or non-readonly cable command is performed.

The programming plug and external mic share hardware; cable-connected RF or
PCM tests do not independently establish valid speech from the analog mic.
"""
from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from types import SimpleNamespace

RUN = re.compile(r'^K6 MOD run serial=(\d+)\b')
RETURN = re.compile(r'^K6 MOD ret=(-?\d+)\b')


def consume_line(line: str, serial: int, active: bool) -> tuple[bool, bool]:
    """Return (target_active, target_returned); ignore unrelated modules."""
    m = RUN.match(line)
    if m:
        return int(m.group(1)) == serial, False
    if active and RETURN.match(line):
        return False, True
    return active, False


def observe(dev, *, module_serial: int, timeout: float, on_line=print,
            clock=time.monotonic, pause=time.sleep) -> tuple[list[str], str]:
    """Receive console text passively until this module returns; bounded input.

    No writes, cable handshakes, RX buffer resets or radio state changes.
    """
    if timeout <= 0 or module_serial <= 0:
        raise ValueError('finite positive timeout and expected module serial required')
    deadline = clock() + timeout
    active = False
    raw = bytearray()
    lines: list[str] = []
    while clock() < deadline:
        chunk = dev.read(256)
        if not chunk:
            pause(0.01)
            continue
        for ch in chunk:
            if ch in (10, 13):
                if not raw:
                    continue
                line = raw.decode('ascii', 'replace').strip()
                raw.clear()
                if not line:
                    continue
                lines.append(line)
                on_line(line)
                active, done = consume_line(line, module_serial, active)
                if done:
                    return lines, line
            elif len(raw) < 1024:
                raw.append(ch)
            else:
                # A malformed/binary line must never trigger a false module match.
                raw.clear()
    raise TimeoutError('expected GP0 module start/return not observed; no cable handshake sent')


def capture_after_return(dev, *, serial: int, lines: list[str], return_line: str,
                         save: Path, logger=print) -> dict:
    """Single cable session, both read-only queries; save raw bytes BEFORE end."""
    from k6_install import Cable, CABLE_HANDSHAKE
    from k6_sotto_gp0_fault import decode as decode_fault
    from k6_sotto_gp0_timing import decode_timing

    record = {'module_serial_expected': serial,
              'console_lines': lines, 'console_return_line': return_line,
              'fault_raw_hex': None, 'timing_raw_hex': None,
              'fault': None, 'timing': None, 'decode_error': None}
    if save.exists():
        raise FileExistsError(f'refusing to overwrite diagnostic {save}')
    cable = Cable(dev, logger)
    if not cable.handshake(CABLE_HANDSHAKE, wait=10):
        raise RuntimeError('no cable response after module returned; no reboot requested')
    try:
        try:
            info = cable.info()
            if info.get('protocol') != 5 or info.get('arena') != 14976:
                raise ValueError(f'unexpected firmware protocol/arena: {info!r}')
            f = cable.call(0x75)
            record['fault_raw_hex'] = f.hex()
            t = cable.call(0x76)
            record['timing_raw_hex'] = t.hex()
            record['fault'] = decode_fault(f)
            record['timing'] = decode_timing(t, record['fault'])
            if not record['fault']['valid'] or record['fault']['module_serial'] != serial:
                raise ValueError('completed GPF1 record was not for expected GP0 run')
        except Exception as err:
            record['decode_error'] = f'{type(err).__name__}: {err}'
            raise
        finally:
            # A malformed parser or a UART glitch must not destroy the only
            # physical run's raw diagnostics before writing them to disk.
            save.parent.mkdir(parents=True, exist_ok=True)
            with save.open('x') as out:
                json.dump(record, out, indent=2, sort_keys=True)
                out.write('\n')
    finally:
        cable.end()  # Resets receiver; only after module exited & data saved.
    return record


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--port', default='/dev/ttyUSB0')
    p.add_argument('--module-serial', type=int, required=True,
                   help='expected SOTTO GP0 module serial; verify with prior catalog')
    p.add_argument('--timeout', type=float, default=120,
                   help='bounded seconds listening for one operator-triggered run')
    p.add_argument('--save', type=Path,
                   help='new JSON path for optional dual GPF1/GPT1 capture after return')
    p.add_argument('--ack-reboot', action='store_true',
                   help='required with --save: readout ends cable session and resets radio')
    a = p.parse_args()
    if a.save is not None and not a.ack_reboot:
        p.error('--save requires --ack-reboot (readout ends by restarting radio)')
    if a.save is None and a.ack_reboot:
        p.error('--ack-reboot applies only to --save')
    if a.save and a.save.exists():
        p.error('refusing to overwrite an existing capture file')
    from k6_install import open_port
    from k6_port_lock import serial_lock
    with serial_lock(a.port):
        with open_port(SimpleNamespace(port=a.port)) as dev:
            lines, last = observe(dev, module_serial=a.module_serial, timeout=a.timeout,
                                  on_line=lambda s: print(s, flush=True))
            print(f'Observed GP0 module return: {last}', flush=True)
            if a.save:
                result = capture_after_return(dev, serial=a.module_serial,
                                              lines=lines, return_line=last, save=a.save)
                print(json.dumps({'fault': result['fault'], 'timing': result['timing']},
                                 indent=2), flush=True)
            else:
                print('Passive-only: did not transmit any bytes or reset the radio', flush=True)


if __name__ == '__main__':
    main()
