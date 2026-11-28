#!/usr/bin/env python3
"""Stage a flash-ready Sotto GP0 TX candidate without contacting a radio.

Includes the actual firmware image, pinned module, K6CB prerequisite,
K6SC deck, radio slot-independent module installer manifest, and immutable
hashes. This does NOT flash, sign, install, or claim real RF qualification.
"""
from __future__ import annotations
import argparse
import ctypes as C
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import zlib

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from modem import k6mod
from tools.k6_sotto_module import (build as module_build, SOTTO_ARENA_BYTES,
    SOTTO_SCRATCH_RESERVE,SOTTO_MODULE_OVERHEAD,SOTTO_LIVE_SCRATCH_REQUIRED,FIXED)
from tools.k6_sotto_ivf_deck import build_deck,SLIM_CELLS


def hash_bytes(b):return hashlib.sha256(b).hexdigest()


def codebook_bytes(temp:Path)->bytes:
    so=temp/'sotto-reference.so'
    subprocess.run(['cc','-O2','-shared','-fPIC','-DSOTTO_V36',
                    '-DSOTTO_PITCH_THIRD','-DFX_NARROW_MUL',
                    str(FIXED/'sotto_fx_slim.c'),str(FIXED/'fx_tables.c'),'-o',str(so)],check=True)
    obj=C.CDLL(str(so))
    table=b''.join(bytes((C.c_char*n).in_dll(obj,f'fx_cb{i}'))
                   for i,n in enumerate((10240,10240,5120,5120)))
    h=bytearray(32)
    struct.pack_into('<4sHHI',h,0,b'K6CB',1,20,len(table))
    h[12:17]=bytes([4,9,9,8,8])
    struct.pack_into('<I',h,24,zlib.crc32(table))
    struct.pack_into('<I',h,28,zlib.crc32(h[:28]))
    assert len(table)==30720
    return bytes(h)+table


def build_release(firmware:Path,dest:Path):
    if dest.exists():raise FileExistsError(f'Preserving existing release folder: {dest}')
    image=firmware.read_bytes()
    if not 90000<=len(image)<=122880:raise ValueError(f'Firmware flash size invalid: {len(image)}')
    # This release is for packed KUL2 SPI. Fixed-slot firmware boots but
    # cannot enumerate that directory. The compiled magic is an additional
    # preflight gate, not a replacement for testing the actual module reader.
    if b'KUL2' not in image:
        raise ValueError('Firmware lacks KUL2 support; refusing packed-library GP0 release')
    # Cable-only K6 builds intentionally omit the 16-byte signing placeholder;
    # keyed OTA builds use exactly one. Reject ambiguous images, and never
    # package the user's personal signing key.
    placeholders=image.count(k6mod.PLACEHOLDER)
    if placeholders not in (0,1):
        raise ValueError('Ambiguous firmware module-key placeholders')
    module,entry=module_build(host_resources=True,gp0_irq_tx=True)
    if entry!=16 or k6mod.module_name(module)!='SOTTO GP0':
        raise ValueError('Wrong target module or entry point')
    scratch=SOTTO_ARENA_BYTES-SOTTO_SCRATCH_RESERVE-len(module)-SOTTO_MODULE_OVERHEAD
    if scratch<SOTTO_LIVE_SCRATCH_REQUIRED:
        raise ValueError('Module and encoder exceed permitted scratch arena')
    with tempfile.TemporaryDirectory() as t:
        cb=codebook_bytes(Path(t))
    deck=build_deck(SLIM_CELLS.read_text())
    if deck[:4]!=b'K6SC' or cb[:4]!=b'K6CB':
        raise ValueError('Codec resource format mismatch')
    dest.mkdir(parents=True)
    payloads={'firmware-gp0-tx.bin':image,'module.bin':module,
              'sotto-1200-k6cb.bin':cb,'sotto-ivf.k6sc':deck}
    for name,data in payloads.items():(dest/name).write_bytes(data)
    module_dir=dest/'module-bundle';module_dir.mkdir()
    (module_dir/'module.bin').write_bytes(module)
    installer={'format':1,'build':'Sotto GP0 TX module test candidate',
               'build_id':'0000000000000000',
               'library':[{'id':'ag7su-sotto-gp0-tx','kind':'module',
                           'file':'module.bin','name':'SOTTO GP0','level':30}],
               'sha256':{'module.bin':hash_bytes(module)}}
    (module_dir/'manifest.json').write_text(json.dumps(installer,indent=2)+'\n')
    info={'format':1,'candidate':'Sotto GP0 half-duplex TX test candidate',
          'flashable_image':True,'flashed_to_radio':False,
          'module_key_placeholder_present':bool(placeholders),
          'build_profile':'cable-only GP0 TX candidate',
          'end_to_end_virtual_rf_tested':True,'on_radio_encoder_tested':False,
          'on_radio_receiver_included':False,
          'firmware_api_level':30,'firmware_size_bytes':len(image),
          'firmware_flash_headroom':122880-len(image),
          'module_size_bytes':len(module),'module_entry':entry,
          'module_scratch_available':scratch,'module_scratch_required':SOTTO_LIVE_SCRATCH_REQUIRED,
          'module_scratch_margin':scratch-SOTTO_LIVE_SCRATCH_REQUIRED,
          'resources':['K6CB at SPI 0x100000','K6SC IVF deck in packed library'],
          'library_layout':'KUL2',
          'modules_install_as_separate_bundle':True,
          'sha256':{k:hash_bytes(v) for k,v in payloads.items()}}
    (dest/'release.json').write_text(json.dumps(info,indent=2)+'\n')
    lines=['# Sotto GP0 — flashable TX test candidate','',
           '**Not yet flashed or physically qualified for Sotto voice.** The prior MIC IRQ hardware',
           'capture was verified; this new firmware-owned GP0 service and encoder require',
           'an attended dummy-load/SDR session before production use.','',
           '## What is included','',
           '- `firmware-gp0-tx.bin`: experimental KD32F328 firmware (API30, no on-radio 4800-baud RX).',
           '- `module-bundle/`: slot-independent `SOTTO GP0` loadable TX module.',
           '- `sotto-1200-k6cb.bin`: pinned read-only Sotto codebook prerequisite (SPI address 0x100000).',
           '- `sotto-ivf.k6sc`: pinned IVF search deck prerequisite (typically cards slot 15).',
           '- `release.json` and `SHA256SUMS`: exact artifact identities.','',
           '## Installation prerequisites (NOT AUTOMATED)','',
           'This is a **cable-only** firmware build; it has no OTA signing-key placeholder.',
           'It must have `K6_USER_STORE=1` for the existing packed KUL2 SPI directory.',
           'Do not flash legacy fixed-slot firmware on a KUL2 radio or distribute personal module keys.','',
           '1. Verify the radio is idle on the authorized dummy load. Claim the bench and back up'
           '   the entire SPI flash; save and verify its SHA256 before changing firmware.',
           '2. Compare `tools/k6_install.py --port /dev/ttyUSB0 status` with this manifest.',
           '   Ensure the K6CB resource is present at 0x100000 and the IVF deck matches;',
           '   do NOT overwrite calibration, codebooks, or any existing slot blindly.',
           '3. All current 0–31 module/card slots are occupied. Choose a deliberate replacement',
           '   destination and back up its exact signed envelope before installation.',
           '   Preserve `MIC IRQ` slot 25 and `SOTTO MIC` slot 30 as recovery paths.',
           '4. After an attended review, run the existing standard firmware installer:',
           '   `python3 tools/k6_install.py --port /dev/ttyUSB0 flash firmware-gp0-tx.bin`',
           '   and follow its explicit confirmations. No automatic flash is performed by this release.',
           '5. Then install `module-bundle` using the installer only after verifying which occupied',
           '   slot it would replace. Confirm exact readback, API30 and the module key.',
           '6. Disconnect the programming cable for real microphone/PTT; monitor a bounded',
           '   dummy-load TX with an independent SDR receiver tuned to the active VFO.',
           '   Reconnect without reboot only when capturing boot-local diagnostic results.','',
           '## Proven vs unproven','',
           'The compiled SOTTO GP0 module passed virtual PCM→encoder→FSK→independent RX',
           'tests, including a PTT release 13 samples into an 80-sample block, exact decoded',
           'codec bytes, end-of-session frame and nonblocking RF drain. The linked ARM API30',
           'GP0 service separately passed start/cleanup gates. These are **not** the same as',
           'real microphone/codec/RF concurrency on the physical MCU. That qualification',
           'remains the next attended hardware step. A native 4800-baud receiver is excluded',
           'from this size-constrained TX image, so reception currently relies on the existing',
           'independent SDR/host decoder.','']
    (dest/'README.md').write_text('\n'.join(lines))
    hashes=[f'{hash_bytes((dest/n).read_bytes())}  {n}' for n in sorted(payloads)]
    hashes.extend([f'{hash_bytes((dest/n).read_bytes())}  {n}' for n in ('release.json','README.md')])
    (dest/'SHA256SUMS').write_text('\n'.join(hashes)+'\n')
    # Verify the installer's own manifest reader with no network or serial.
    sys.path.insert(0,str(ROOT/'tools'))
    from k6_install import load_bundle
    x=load_bundle(str(module_dir))
    assert x['library'][0]['level']==30
    return info


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--firmware',required=True,type=Path)
    p.add_argument('--output',required=True,type=Path)
    a=p.parse_args()
    info=build_release(a.firmware.resolve(),a.output.resolve())
    archive=a.output.with_suffix('.tar.gz')
    with tarfile.open(archive,'w:gz') as t:t.add(a.output,arcname=a.output.name)
    print(json.dumps({'path':str(a.output),'archive':str(archive),**info},indent=2))
if __name__=='__main__':main()
