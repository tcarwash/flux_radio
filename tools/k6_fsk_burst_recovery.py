#!/usr/bin/env python3
"""Run real compiled Cortex-M0 TX/RX fixtures across consecutive RF bursts.

Faults modify *virtual air* between compiled TX and independent RX: whole
burst erasures, corrupted payload bits, FIFO starvation and clock mismatch.
The receiver never receives expected payloads. Its raw FIFO data is parsed
using production pager CRC/framing, then loss-aware sequence bookkeeping.
"""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
import numpy as np
from modem import pager
from sim.k6emu.rf.virtual_link import Channel,VirtualAir,AirSource
from sim.k6emu.rf.backend import RFBackend
from tools.k6_fsk_virtual_link import transmit,HZ,score
from tools.k6_fsk_emulator_replay import receive

PROFILE=pager.profile('L','1200',depth=1,parity=False,short_last=True)


def decode_sequences(frames, *, msg_id=73, expected_packets=None):
    """Process RX FIFO bytes without expected packet data or sequence oracle.

    Bad CRC/format frames are quarantined. Unique accepted sequences are
    assembled by their *on-air* CRC-valid header; never infer a missing packet
    from a corrupt FIFO frame. Duplicate valid packets are counted but not
    reinserted, and a final replay cannot claim complete delivery with holes.
    """
    if expected_packets is not None and (not isinstance(expected_packets,int) or
                                          not 1<=expected_packets<=255):
        raise ValueError('expected_packets must be 1..255')
    accepted={}
    corrupt=foreign=duplicate=out_of_range=0
    header_total=None
    for raw in frames:
        info=PROFILE.unpack(raw)
        if info is None:
            corrupt+=1
            continue
        if info['msg_id']!=msg_id or info['parity'] is not None:
            foreign+=1
            continue
        seq=info['seq']
        if header_total is None:
            header_total=info['total']
        if info['total']!=header_total:
            foreign+=1
            continue
        if expected_packets is not None and seq>=expected_packets:
            out_of_range+=1
            continue
        if seq in accepted:
            duplicate+=1
            continue
        accepted[seq]=raw
    missing=([k for k in range(expected_packets) if k not in accepted]
             if expected_packets is not None else [])
    return dict(sequences=sorted(accepted),missing_sequences=missing,
                accepted_packets=len(accepted),crc_invalid=corrupt,
                foreign_packets=foreign,duplicate_packets=duplicate,
                out_of_range_packets=out_of_range,
                header_total_frames=header_total,
                complete=(expected_packets is not None and not missing))


def trial(*,packets=5,drop_bursts=(),corrupt_bursts=(),clock_ppm=0.,
          cn_db=25,offset_hz=0.,poll_us=1000,threshold=4,
          lead_us=30000,gap_us=12000,seed=1):
    if (not isinstance(packets,int) or not 2<=packets<=12 or
        not 1<=threshold<=7 or not 100<=poll_us<=50000 or
        not all(isinstance(x,int) and 0<=x<packets for x in (*drop_bursts,*corrupt_bursts)) or
        any(x in drop_bursts for x in corrupt_bursts) or
        not math.isfinite(clock_ppm) or abs(clock_ppm)>30000):
        raise ValueError('invalid test matrix or contradictory burst fault indices')
    rng=np.random.default_rng(20261124)
    frames=PROFILE.pack(rng.bytes(packets*8*6),msg_id=73)
    if len(frames)!=packets:raise RuntimeError('profile produced wrong packet count')
    expected=[p for p in frames]
    channel=Channel(clock_ppm=clock_ppm,cn_db=cn_db,offset_hz=offset_hz,seed=seed)
    air=VirtualAir(channel)
    tx=transmit(air,expected,rate=240000,tune=HZ,gap_us=gap_us,lead_us=lead_us)
    # Edits are strictly virtual-air, not TX-compiled C code or RF registers.
    for k in corrupt_bursts:
        # 160 marker bits precede payload. A single invalidated CRC-protected
        # payload bit is an erasure at packet reassembly, not a 'recovered' byte.
        air.bursts[k].bits[160+13] *= -1
    for k in sorted(drop_bursts,reverse=True):
        del air.bursts[k]
    end=lead_us/1e6+packets*((20+64)*8/(4800*(1+clock_ppm/1e6))+gap_us/1e6+.02)+.07
    backend=RFBackend(AirSource(air,240000,HZ))
    try:
        raw,rx=receive(backend,tune=HZ,duration=end,frame_bytes=64,
                       threshold=threshold,poll_us=poll_us)
    finally:backend.close()
    assembled=decode_sequences(raw,expected_packets=packets)
    independent=score(expected,raw,guard=False)
    return dict(kind='compiled_cortex_m0_multiburst_offline_virtual_air',
                rf_hardware=False,physical_tx=False,
                packets=packets,drop_bursts=list(drop_bursts),
                corrupt_bursts=list(corrupt_bursts),clock_ppm=clock_ppm,
                cn_db=cn_db,offset_hz=offset_hz,poll_us=poll_us,
                tx_completed=tx['completed'],
                receiver_frames=len(raw),
                last_acquired_clock_ppm=rx['fsk']['last_acquired_clock_ppm'],
                clock_fits=rx['fsk']['clock_fits'],
                rx_overruns=rx['fsk']['overruns'],
                rx_underflows=rx['fsk']['underflows'],
                **assembled,**{k:independent[k] for k in
                             ('raw_exact_frames','exact_packets','packet_delivery')},
                clean_recovery=(assembled['sequences']==
                                 [k for k in range(packets)
                                  if k not in set(drop_bursts)|set(corrupt_bursts)]
                                 and len(raw)==packets-len(drop_bursts)
                                 and assembled['crc_invalid']==len(corrupt_bursts)
                                 and assembled['accepted_packets']>0
                                 and rx['fsk']['overruns']==rx['fsk']['underflows']==0))


def trial_parity(*,data_packets=4,drop_bursts=(),corrupt_bursts=(),
                 clock_ppm=0.,cn_db=25,offset_hz=0.,poll_us=1000,
                 threshold=4,seed=1):
    """Real compiled RF-burst tests of the *existing* L64 parity assembler.

    4 data packets plus 1 XOR parity packet compose the first group. One bad
    or absent data packet is recovered from the on-air parity and remaining
    CRC-valid packets; two missing data packets in a group cannot be guessed.
    """
    if not isinstance(data_packets,int) or not 2<=data_packets<=12:
        raise ValueError('data_packets must be 2..12')
    if (not 1<=threshold<=7 or not 100<=poll_us<=50000 or
        not math.isfinite(clock_ppm) or abs(clock_ppm)>30000):
        raise ValueError('invalid poll or clock configuration')
    prof=pager.profile('L','1200',depth=4,parity=True,short_last=True)
    original=np.random.default_rng(20261124).bytes(data_packets*8*6)
    expected=prof.pack(original,msg_id=73)
    total=len(expected)
    if not all(isinstance(n,int) and 0<=n<total for n in (*drop_bursts,*corrupt_bursts)):
        raise ValueError('fault indices must refer to on-air data/parity bursts')
    if len(set(drop_bursts))!=len(drop_bursts) or len(set(corrupt_bursts))!=len(corrupt_bursts):
        raise ValueError('duplicate fault index')
    if set(drop_bursts)&set(corrupt_bursts):
        raise ValueError('cannot drop and corrupt the same on-air burst')
    air=VirtualAir(Channel(clock_ppm=clock_ppm,cn_db=cn_db,
                           offset_hz=offset_hz,seed=seed))
    lead_us=30000; gap_us=12000
    tx=transmit(air,expected,rate=240000,tune=HZ,gap_us=gap_us,lead_us=lead_us)
    for k in corrupt_bursts:
        air.bursts[k].bits[160+13] *= -1
    for k in sorted(drop_bursts,reverse=True):
        del air.bursts[k]
    duration=lead_us/1e6+total*((20+64)*8/(4800*(1+clock_ppm/1e6))
                                +gap_us/1e6+.02)+.07
    backend=RFBackend(AirSource(air,240000,HZ))
    try:
        received,rx=receive(backend,tune=HZ,duration=duration,
                            frame_bytes=64,threshold=threshold,poll_us=poll_us)
    finally:backend.close()
    assembler=pager.Assembler(prof)
    for frame in received:
        assembler.push(frame)
    if 73 in assembler.messages:
        restored,good=assembler.frames(73)
        good=np.asarray(good,dtype=np.uint8)
        all_good=bool(np.all(good)) and len(restored)==len(original)
        exact=bool(all_good and restored==original)
        valid_slots=int(good.sum())
    else:
        restored=b'';good=np.zeros(data_packets*8,dtype=np.uint8)
        exact=False;valid_slots=0
    return dict(kind='compiled_cortex_m0_FSK_plus_existing_XOR_parity_assembler',
                rf_hardware=False,physical_tx=False,
                data_packets=data_packets,on_air_packets=total,
                drop_bursts=list(drop_bursts),corrupt_bursts=list(corrupt_bursts),
                clock_ppm=clock_ppm,cn_db=cn_db,offset_hz=offset_hz,
                poll_us=poll_us,tx_completed=tx['completed'],
                receiver_frames=len(received),
                clock_fits=rx['fsk']['clock_fits'],
                last_acquired_clock_ppm=rx['fsk']['last_acquired_clock_ppm'],
                crc_invalid=assembler.bad,
                fifo_overruns=rx['fsk']['overruns'],
                fifo_underflows=rx['fsk']['underflows'],
                repaired_data_packets=assembler.rebuilt,
                recovered_voice_slots=valid_slots,
                total_voice_slots=data_packets*8,
                payload_exact=exact,
                pass_qualified=(exact and rx['fsk']['overruns']==0
                                and rx['fsk']['underflows']==0),
                voice_slot_good_flags=good.astype(int).tolist())


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--packets',type=int,default=5)
    p.add_argument('--parity',action='store_true',help='existing L64 4-packet XOR parity assembler, not no-parity packet ledger')
    p.add_argument('--drop',type=int,action='append',default=[])
    p.add_argument('--corrupt',type=int,action='append',default=[])
    p.add_argument('--clock-ppm',type=float,default=0)
    p.add_argument('--poll-us',type=int,default=1000)
    p.add_argument('--cn-db',type=float,default=25)
    p.add_argument('--offset-hz',type=float,default=0)
    p.add_argument('--json',type=Path)
    args=p.parse_args(argv)
    try:
        if args.parity:
            result=trial_parity(data_packets=args.packets,drop_bursts=args.drop,
                                corrupt_bursts=args.corrupt,clock_ppm=args.clock_ppm,
                                poll_us=args.poll_us,cn_db=args.cn_db,offset_hz=args.offset_hz)
        else:
            result=trial(packets=args.packets,drop_bursts=args.drop,
                         corrupt_bursts=args.corrupt,clock_ppm=args.clock_ppm,
                         poll_us=args.poll_us,cn_db=args.cn_db,offset_hz=args.offset_hz)
    except (ValueError,RuntimeError) as exc:p.error(str(exc))
    if args.json:
        args.json.parent.mkdir(parents=True,exist_ok=True)
        args.json.write_text(json.dumps(result,indent=2)+'\n')
    if args.parity:
        print('Compiled parity bursts: voice slots {}/{}, parity-rebuilt packets {}, bad CRC {}, exact={}'.format(
            result['recovered_voice_slots'],result['total_voice_slots'],
            result['repaired_data_packets'],result['crc_invalid'],result['pass_qualified']))
        return 0 if result['pass_qualified'] else 1
    print('Compiled FSK bursts: packet delivery {}/{}; accepted sequences={} missing={}, bad CRC={} overrun={} recovered={}'.format(
        result['exact_packets'],result['packets'],result['sequences'],
        result['missing_sequences'],result['crc_invalid'],result['rx_overruns'],
        result['clean_recovery']))
    return 0 if result['clean_recovery'] else 1

if __name__=='__main__':raise SystemExit(main())
