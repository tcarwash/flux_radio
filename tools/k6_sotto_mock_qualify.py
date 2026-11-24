#!/usr/bin/env python3
"""One-command NO-HARDWARE Sotto + KISS mock qualification.

Runs a constrained set of known hardware-free fake-device/ABI pytest tests,
then executes the *actual* ARM Sotto live module in Unicorn against virtual
PCM -> FSK -> IQ -> independent native-C/Python wire decoders. No serial, SDR,
bench lease, radio transmission, install, or firmware flashing is possible.
The fake KISS device does NOT emulate Sotto voice or advertise it as packet TX.
Timing, ISR, SPI and radio-chip models are estimates; not hardware evidence.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from sim.k6emu.rf.virtual_link import Channel
from tools.k6_sotto_virtual_link import accepted,prepare,trial
from tools.k6_sotto_opt_bench import mixed_pcm

# Do not add hardware-capable tests here; the whole purpose is no-device mode.
MOCK_TESTS=(
    'tests/test_k6_kiss_hil_fake_device.py',
    'tests/test_k6_mode_registry.py',
    'tests/test_k6_sotto_installed_vfo.py',
    'tests/test_k6_packet_dispatch.py',
    'tests/test_k6_packet_mailbox_contract.py',
    'tests/test_k6_sotto_virtual_link.py',
)

# Named model presets. The final preset is deliberately DIAGNOSTIC: failing
# because of PCM pressure is expected to be possible and is not a failed smoke.
PRESETS=(
    ('nominal',4800,10,0,True),
    ('slower_spi',4800,34,0,True),
    ('high_load',5745,34,0,False),
)


def summarize(result):
    """Human-sized excerpt while retaining byte-exact and safety evidence."""
    keys=('accepted','module_return','module_bytes','samples','samples_expected',
          'pcm_lost','pcm_highwater','lease_expired','codec_frames','codec_exact',
          'received','wire_decoder_agrees','stack_bytes','spi_reads',
          'spi_read_bytes','keyed_reads','keyed_read_bytes','first_rf_ms')
    fields={k:result[k] for k in keys}
    fields['session_complete']=result['session']['complete']
    fields['shifted_packets']=result['session']['offset_2']
    fields['rx_clean']=result['rx'] is not None and all(result['rx'][k]==0 for k in ('gaps','underflows','overruns'))
    fields['tx_idle']=not bool(result['tx']['active'])
    fields['tx_aborted']=result['tx']['aborted']
    fields['tx_clean']=(fields['tx_idle'] and result['tx']['aborted']==result['tx']['rejected']==result['tx']['overflows']==0)
    return fields


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sotto-tree',type=Path,required=True,
                   help='checkout containing experimental API29 Sotto live module (not main alone)')
    p.add_argument('--json',type=Path,help='write machine-readable qualification report')
    p.add_argument('--skip-mock-tests',action='store_true',
                   help='only run the real ARM codec/virtual RF (not the separate KISS fake)')
    p.add_argument('--quick',action='store_true',
                   help='only the triangular fixture; normal, slow SPI, and high-load presets')
    p.add_argument('--spi-byte-us',type=float,default=0,
                   help='per-byte SPI model in microseconds (uncalibrated; default legacy zero)')
    a=p.parse_args(argv)
    if not (a.sotto_tree/'c/k6/mod/sotto_live_tx.c').is_file():
        p.error('--sotto-tree must contain the actual experimental c/k6/mod/sotto_live_tx.c')
    if not 0<=a.spi_byte_us<=100:
        p.error('spi-byte-us must be in 0..100')
    if a.spi_byte_us!=a.spi_byte_us or a.spi_byte_us==float('inf'):
        p.error('spi-byte-us must be finite')

    start=time.perf_counter()
    report=dict(schema=1,kind='offline_mock_only',
                repository='flux_radio',
                limitations=['KISS fake serial and Sotto virtual RF are independent test layers',
                             'Sotto is voice-only, not an API27/KISS packet provider',
                             'no physical RF, device ISR/SPI timing, DAC, microphone or audio-quality qualification'],
                source_tree=str(a.sotto_tree.resolve()),
                model=dict(cpi=1.5,spi_byte_us=a.spi_byte_us),
                mock_tests=None,trials=[],pass_all=False)
    passed=True
    if not a.skip_mock_tests:
        cmd=[sys.executable,'-m','pytest','-q',*MOCK_TESTS]
        env=os.environ.copy();env['FLUX_SOTTO_TREE']=str(a.sotto_tree.resolve())
        try:
            completed=subprocess.run(cmd,cwd=ROOT,capture_output=True,text=True,
                                     timeout=180,env=env)
            merged=(completed.stdout+'\n'+completed.stderr).strip()
            m=re.search(r'(\d+) passed',merged)
            report['mock_tests']=dict(exit_code=completed.returncode,
                                      passed=int(m.group(1)) if m else None,
                                      tail=merged[-1400:])
            passed=completed.returncode==0
            print('Mock KISS/ABI + Sotto tests:',merged.splitlines()[-1] if merged else '(no output)',flush=True)
        except subprocess.TimeoutExpired:
            report['mock_tests']=dict(exit_code=None,passed=None,tail='timeout (180s)')
            passed=False
            print('Mock KISS/ABI + Sotto tests: timed out',flush=True)
    if passed:
        try:
            for fixture_name,pcm in ([('triangle-1.04s',None)] if a.quick else
                                     [('triangle-1.04s',None),('mixed-1.2s',mixed_pcm())]):
                with tempfile.TemporaryDirectory(prefix='flux-sotto-mock-') as d:
                    art=prepare(a.sotto_tree,Path(d),pcm=pcm)
                    report['sotto_revision']=art['revision']
                    report['sotto_tracked_dirty']=art['dirty']
                    for preset,adc,spi,prefix,required in PRESETS:
                        result=trial(art,Channel(prefix_every=prefix),cpi=1.5,
                                     adc_cycles=adc,spi_us=spi,spi_byte_us=a.spi_byte_us)
                        result['accepted']=accepted(result)
                        outcome=summarize(result)
                        report['trials'].append(dict(fixture=fixture_name,preset=preset,
                                                     adc_cycles=adc,spi_us=spi,
                                                     required_pass=required,**outcome))
                        if required and not outcome['accepted']:
                            passed=False
                        if not outcome['tx_idle']:
                            passed=False
                        print(f'{fixture_name:16} {preset:12} '
                              f'{"PASS" if outcome["accepted"] else "MODEL_LIMIT"} '
                              f'{outcome["samples"]}/{outcome["samples_expected"]} PCM',flush=True)
                    if fixture_name=='triangle-1.04s':
                        alignment=trial(art,Channel(prefix_every=2),cpi=1.5,
                                        adc_cycles=4800,spi_us=10,spi_byte_us=a.spi_byte_us)
                        alignment['accepted']=accepted(alignment)
                        outcome=summarize(alignment)
                        report['trials'].append(dict(fixture=fixture_name,preset='offset2',
                                                     adc_cycles=4800,spi_us=10,
                                                     required_pass=True,**outcome))
                        passed=passed and outcome['accepted'] and outcome['tx_clean'] and outcome['shifted_packets']==3
                        print(f'{fixture_name:16} offset2      '
                              f'{"PASS" if outcome["accepted"] else "FAIL"} '
                              f'{outcome["samples"]}/{outcome["samples_expected"]} PCM',flush=True)
        except Exception as exc:
            report['error']=f'{type(exc).__name__}: {exc}'
            passed=False
            print(f'Offline mock failure: {report["error"]}',file=sys.stderr)
    report['pass_all']=passed
    report['wall_seconds']=round(time.perf_counter()-start,3)
    if a.json:
        a.json.parent.mkdir(parents=True,exist_ok=True)
        a.json.write_text(json.dumps(report,indent=2)+'\n')
    print(f'Mock qualification: {"PASS" if passed else "FAIL"}',flush=True)
    return 0 if passed else 1


if __name__=='__main__':sys.exit(main())
