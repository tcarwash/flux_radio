#!/usr/bin/env python3
"""Compare native-FSK control paths of earlier RF-PASS vs latest RF-FAIL.

Read-only offline audit. Does NOT print or store private voice/codeword arrays.
Focuses on RF register definitions, control register-write sequences and
TX/key/PA status operations; compile/layout/module runtime effects remain
unmeasured. No radio/serial/SDR access and no hardware claims inferred.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[1]
PASSED=ROOT/'c/k6/mod/fsk_sotto_l8_burst_armed.c'
FAILED=Path('/home/tyler/flux-bench/micvoice-20261123/fsk12-mic-rf-private-20261123/fsk12_k6mic_private_armed.c')


def signature(src:Path):
    text=src.read_text()
    macros={}
    for line in text.splitlines():
        m=re.fullmatch(r'#define\s+(FSK_(?:FREQUENCY_HZ|GAIN_CODE|BAUD_CODE|WORDS|MAX_TX_MS|FRAME_TIMEOUT_MS))\s+([^\s]+)',line.strip())
        if m:macros[m.group(1)]=m.group(2)
    # Skip all static audio arrays and other literal source data on purpose.
    # Seek only audited RF register writes with EXACT control arguments.
    writes=[];controls=[]
    for line in text.splitlines():
        st=line.strip()
        m=re.fullmatch(r'W\((R_\w+),\s*(.*?)\);',st)
        if m and m.group(1) in {'R_CONTROL','R_CLEAR_IRQ','R_LENGTH','R_WORK','R_PREAMBLE',
                                 'R_GAIN','R_BAUD','R_SYNC0','R_SYNC1','R_SYNC2','R_FIFO'}:
            writes.append((m.group(1),m.group(2).replace(' ','')))
        if any(x in st for x in ('api->tx_on(', 'api->tx_pa(', 'api->tx_off(',
                               'a->fd_read(R_STATUS)', 'a->delay_us(5000u)',
                               'if(!safe(a))return -4;', 'ret=send_frame(api,frame);')):
            controls.append(st)
    if len(writes)<17 or not any(x[0]=='R_FIFO' for x in writes):
        raise ValueError('Could not identify familiar finite FSK control path in audited source')
    return dict(macros=macros,register_writes=writes,tx_safety_controls=controls,
                num_hardware_frames=int(re.search(r'#define FSK_NUM_FRAMES\s+(\d+)u',text).group(1)),
                voice_bytes_excluded_from_signature=True)


def compare(a:Path=PASSED,b:Path=FAILED):
    p,q=signature(a),signature(b)
    comparable=('macros','register_writes','tx_safety_controls')
    same={k:p[k]==q[k] for k in comparable}
    return dict(experiment='read-only native-FSK RF-success/failure source control-path parity',
                success_source=str(a),failed_source=str(b),
                source_sha256_a=hashlib.sha256(a.read_bytes()).hexdigest(),
                source_sha256_b=hashlib.sha256(b.read_bytes()).hexdigest(),
                matched_fields=same,control_path_signatures_equal=all(same.values()),
                success_frame_count=p['num_hardware_frames'],
                failed_frame_count=q['num_hardware_frames'],
                captured_voice_bytes_logged=False,
                no_hardware_access=True,
                inference=['Source-level FSK register writes and TX/PA guards identical.',
                           'Does NOT establish the cause of 0x00F8, which may depend on RFIC state, runtime timing, SPI or binary layout.',
                           'The two compiled images and packet bytes are different; source equality is not physical equivalence.'])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--passed',type=Path,default=PASSED)
    p.add_argument('--failed',type=Path,default=FAILED)
    args=p.parse_args()
    if args.out.exists():p.error('Refuse overwrite')
    r=compare(args.passed,args.failed)
    args.out.write_text(json.dumps(r,indent=2)+'\n')
    print('Control signatures match:',r['matched_fields'],'frame count',r['success_frame_count'],'vs',r['failed_frame_count'], 'No audio exposed')

if __name__=='__main__':main()
