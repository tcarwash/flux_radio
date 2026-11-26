#!/usr/bin/env python3
"""Fetch read-only, on-device Sotto SPI timings (opt-in diagnostic firmware ONLY).

Never flashes, installs modules, keys PTT, or performs SPI writes. Entering
the cable session interrupts RX and closing it reboots the radio; operator
acknowledgement is required. Command 0x73 does not exist on normal firmware.
"""
import argparse
import json
import struct
import sys
from pathlib import Path
from types import SimpleNamespace

CMD_SOTTO_SPI_BENCH=0x73
RESPONSE_SIZE=24
TICK_US=100

def decode(payload:bytes)->dict:
    if len(payload)!=RESPONSE_SIZE or payload[:4]!=b'SPB1':
        raise ValueError('Invalid SPB1 response; diagnostic unavailable or truncated')
    sample_count, flags = struct.unpack_from('<HH',payload,4)
    if sample_count<16 or sample_count>1024:
        raise ValueError('Invalid sample count in SPI benchmark response')
    times=struct.unpack_from('<4H',payload,8)
    checksums=struct.unpack_from('<2I',payload,16)
    results={}
    for idx,length in enumerate((20,128)):
        old=times[idx*2]
        fast=times[idx*2+1]
        results[str(length)]={
            'baseline_ticks_100us':old,
            'streaming_ticks_100us':fast,
            'baseline_mean_us_per_read':round(old*TICK_US/sample_count,4),
            'streaming_mean_us_per_read':round(fast*TICK_US/sample_count,4),
            'estimated_savings_us_per_read':round((old-fast)*TICK_US/sample_count,4),
            'estimated_savings_percent':(
                round((old-fast)*100/old,3) if old else None),
        }
    return {
       'version':'SPB1',
       'sample_count_per_path_per_size':sample_count,
       'timer_tick_us':TICK_US,
       'flags':flags,
       'baseline_read_error':bool(flags & 1),
       'streaming_read_error':bool(flags & 2),
       'digest_mismatch':bool(flags & 4),
       'spot_check_mismatch':bool(flags & 8),
       'valid':flags==0,
       'read_paths':results,
       'digest_baseline':f'{checksums[0]:08x}',
       'digest_streaming':f'{checksums[1]:08x}',
       'warning':'TIM14 batch times include loop/checksum overhead; they are not per-byte SPI bus timings.',
    }

def run(port):
    from k6_install import Cable, CABLE_HANDSHAKE, open_port
    from k6_port_lock import serial_lock
    with serial_lock(port):
        with open_port(SimpleNamespace(port=port)) as device:
            c=Cable(device,print)
            if not c.handshake(CABLE_HANDSHAKE,wait=10):
                raise RuntimeError('No K6 cable session. No measurement run.')
            try:
                info=c.info()
                caps=c.capabilities()
                if info.get('protocol',0)<5 or caps.get('module_api_max',0)<28:
                    raise RuntimeError('Expected compatible KUL2 firmware; not benchmarking')
                raw=c.call(CMD_SOTTO_SPI_BENCH)
                return {**decode(raw),
                        'device_build_id':info['build_id'].hex() if info['build_id'] else None,
                        'firmware_protocol':info['protocol'],
                        'read_only_cable_session':True}
            finally:
                c.end()

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',default='/dev/ttyUSB0')
    parser.add_argument('--ack-reboot',action='store_true',
        help='Required: cable session interrupts RX and reboots radio at end')
    parser.add_argument('--save',type=Path,
        help='Save JSON locally to a new file; existing files are never overwritten')
    args=parser.parse_args()
    if not args.ack_reboot:
        parser.error('Use --ack-reboot only after confirming radio idle, cable connected and no PTT')
    result=run(args.port)
    output=json.dumps(result,indent=2,sort_keys=True)+'\n'
    if args.save:
        with args.save.open('x') as file:
            file.write(output)
    print(output,end='')
    if not result['valid']:
        parser.exit(2,'Diagnostic returned errors; do not interpret timings as a passed SPI test.\n')

if __name__=='__main__':
    main()
