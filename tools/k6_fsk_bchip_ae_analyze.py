#!/usr/bin/env python3
"""FSK09S-H: archived IQ only, blind sync -> exact 48-word data; B IRQ timeline.

Acquisition uses ONLY public 16x55 preamble + 076b3e36 sync, fixed 4800bps
and no known payload bits. Payload truth and optional leading 0x00F8 are
compared only AFTER bit decisions. Both frame variants must preserve all
first 48 known words, with the last 16-bit guard displaced if prefix occurs.
This RF qualification does NOT alone prove a reliable FIFO AE IRQ: that
requires chip-reported timings and the expected threshold-dependent shift.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from k6_fsk_sotto_sync_rx import find_frames
from k6_fsk_iq_edges import read_iq
from k6_fsk_lab_vector import make_probe_vector

N_DATA_WORDS=48
GUARD=0xa55a
IRQ_FIELDS=("th","bit","evt","done","irq","last","polls","status")


def expected_packet():
    words,_=make_probe_vector(word_count=64,seed=0xACE1)
    return b''.join(w.to_bytes(2,'little') for w in words[:N_DATA_WORDS])


def score_iq(iq,*,min_correlation=.90):
    decision=find_frames(iq,rate_factors=(1.,),min_correlation=min_correlation,
                         payload_bytes=98)
    truth=expected_packet()
    frames=[]
    for p in decision['packets']:
        payload=bytes.fromhex(p['raw_payload_hex'])
        # Optional guard permits either one *known exact* leading control
        # word OR the full intended 98-byte FIFO data. The code must never
        # count 0x00f8 on RF as delivered information.
        clean=payload==truth+GUARD.to_bytes(2,'little')
        prefix=payload[:2]==b'\xf8\x00' and payload[2:]==truth
        frames.append(dict(payload_start_s=p['payload_start_s'],
            baud=p['baud_estimate'],sync_correlation=p['sync_correlation'],
            sync_bit_errors=p['sync_bit_errors'],
            strict_pass=bool(clean or prefix),
            control_prefix_offset_bytes=(2 if prefix else 0 if clean else None),
            data_payload_sha256=hashlib.sha256(payload[2:] if prefix else payload[:96]).hexdigest(),
            exact_48_word_data=bool(clean or prefix),
            full_frame_hash=hashlib.sha256(payload).hexdigest()))
    strict=len(frames)==2 and all(f['strict_pass'] for f in frames)
    return dict(test='FSK-09S-H B chip 2x49word FIFO IRQ qualification: RF only',
        result='RF_PAYLOAD_PASS' if strict else 'RF_PAYLOAD_FAIL',
        expected_frames=2,frames_found=decision['frames_found'],
        programmed_baud=4800,public_sync_only=True,
        expected_48word_data_sha256=hashlib.sha256(truth).hexdigest(),
        zero_payload_bit_errors_if_pass=strict,frames=frames,
        caveat='AE IRQ timing is an independent qualification: RF data PASS does not prove IRQ register semantics.')


def parse_ae_report(report_lines):
    """Parse raw compact telemetry as emitted *after* TX restore, without inference."""
    if not isinstance(report_lines,(list,tuple)):raise ValueError('Expected a list of raw module lines')
    events={}
    for line in report_lines:
        m=re.fullmatch(r'AE([01])\s+(.+)',line)
        if not m:continue
        n=int(m.group(1));ent=events.setdefault(n,{})
        for field,value in re.findall(r'([a-z0-9]+)=([0-9A-F]{4})',m.group(2)):
            if field not in IRQ_FIELDS:raise ValueError('Unknown raw IRQ field')
            if field in ent:raise ValueError('Duplicate IRQ field')
            ent[field]=int(value,16)
    if set(events)!={0,1} or any(set(x)!=set(IRQ_FIELDS) for x in events.values()):
        raise ValueError('Two complete B IRQ traces were not reported')
    if events[0]['th']!=24 or events[1]['th']!=8:
        raise ValueError('Thresholds reported do not match source protocol')
    return events


def extract_module_lines(meta:dict):
    """Accept direct lists, existing reversible-bench JSON or saved IQ summary."""
    if isinstance(meta,list):return meta
    if 'module_ae_lines' in meta:return meta['module_ae_lines']
    transaction=meta.get('hardware_lab_transaction',meta)
    module=transaction.get('module_completion',{})
    if not isinstance(module,dict):raise ValueError('Invalid module transaction')
    if not (transaction.get('original_module_restored') and
            transaction.get('original_radio_frequency_restored') and
            transaction.get('transmission_confirmed_by_firmware') and
            module.get('returned') and module.get('result')==98):
        raise ValueError('No verified completed and restored FSK09S-H transaction')
    return module.get('reports',[])


def assess_ae(raw):
    """Separate observational qualification, never assert B semantics from 1 run."""
    events=parse_ae_report(raw)
    possible=[]
    for key in ('bit','evt'):
        a,b=events[0][key],events[1][key]
        # Expected nominal change is 16 words / 300 words per sec=53.33ms.
        # Require two non-zero genuine observations and a plausible difference;
        # 0xffff means no observation (not a late clock).
        okay=(a!=0xffff and b!=0xffff and 55<=a<=125 and
              110<=b<=190 and 40<=b-a<=67 and a<=events[0]['done'] and b<=events[1]['done'])
        possible.append(dict(mode=key,first_threshold24_ms=None if a==0xffff else a,
            first_threshold8_ms=None if b==0xffff else b,
            measured_delta_ms=None if a==0xffff or b==0xffff else b-a,
            passes_timing_discriminator=bool(okay)))
    return dict(observations=events,possible_interrupt_encodings=possible,
                provisional_b_ae_present=any(x['passes_timing_discriminator'] for x in possible),
                not_yet_safe_for_refill=True,
                qualification='Observe repeated raw traces with SDR and no live writes before enabling refill.')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--iq',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--duration',type=float,default=3.5)
    p.add_argument('--telemetry-json',type=Path,default=None,
                   help='Existing bench .json / IQ summary with verified restored transaction and exact module AE reports')
    a=p.parse_args()
    if a.out.exists():p.error('Refusing to overwrite old RF report')
    rf=read_iq(a.iq,duration=a.duration,offset=280500.)
    score=score_iq(rf)
    with a.iq.open('rb') as f:score['iq_sha256']=hashlib.file_digest(f,'sha256').hexdigest()
    if a.telemetry_json:
        raw=json.loads(a.telemetry_json.read_text())
        score['irq_discriminator']=assess_ae(extract_module_lines(raw))
        score['joint_qualification']='RF_AND_PROVISIONAL_AE' if (
            score['result']=='RF_PAYLOAD_PASS' and
            score['irq_discriminator']['provisional_b_ae_present']) else 'FAIL_OR_INCONCLUSIVE'
    a.out.write_text(json.dumps(score,indent=2)+'\n')
    print(score['result'],'frames',score['frames_found'],'IRQ telemetry included?',bool(a.telemetry_json))

if __name__=='__main__':main()
