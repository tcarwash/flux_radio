#!/usr/bin/env python3
"""Receive-only physical GP0 DMA and concurrent binary IQ/UART lab capture."""
import argparse
import json
from pathlib import Path
import struct
import time
import numpy as np
import serial
from k6_install import CABLE_HANDSHAKE, CMD_END, InstallError, cps_frame
from k6_serial_bench import BenchCable, CMD_BAUD, once
from k6_port_lock import serial_lock

FIELDS = ('status mode ticks produced consumed overruns uncertain max_poll_cycles counts outputs frames tx_drops dma_errors clips cps8 max_lag minimum maximum sum sumsq ratio raw_cap_count final_partial_pairs reserved').split()


def decode_frames(frames, stats):
    chunks=[]; meta=[]; seq_gaps=0; index_gaps=0; nonmonotonic=0
    expected_seq=0; expected_index=None; last_time=None
    for seq,data in frames:
        if len(data)<16: raise ValueError('short sample frame')
        index,stamp,cpb,mode,ratio,zero=struct.unpack('<IIIBBH',data[:16])
        if mode not in (0,1) or not ratio or zero: raise ValueError('invalid sample header')
        if mode!=stats['mode'] or (mode and ratio!=stats['ratio']): raise ValueError('sample configuration differs from final statistics')
        x=np.frombuffer(data[16:],dtype=np.int8).copy()
        if mode and len(x)%2: raise ValueError('odd IQ payload')
        n=len(x)//2 if mode else len(x)
        if seq!=expected_seq: seq_gaps+=1
        expected_seq=seq+1
        if expected_index is not None and index!=expected_index: index_gaps+=1
        expected_index=index+n*64*(ratio if mode else 1)
        if last_time is not None and stamp<=last_time: nonmonotonic+=1
        last_time=stamp
        chunks.append(x);meta.append((seq,index,stamp,cpb,n,mode,ratio))
    samples=np.concatenate(chunks) if chunks else np.array([],np.int8)
    received=len(samples)//2 if stats['mode'] else len(samples)
    return samples,np.array(meta,dtype=np.uint32).reshape(-1,7),dict(sequence_gaps=seq_gaps,index_gaps=index_gaps,nonmonotonic_timestamps=nonmonotonic,frame_count_mismatch=abs(len(frames)-stats['frames']),received_samples=received,missing_samples=max(0,stats['outputs']-received))


def capture(a):
    frames=[];restore=False;result={}
    mode=0 if a.raw else 1
    request=struct.pack('<IIIBBBB',a.hz,a.ms,a.mix,mode,a.ratio,0,0)
    with serial_lock(a.port):
        with serial.Serial(a.port,115200,timeout=.002,write_timeout=2,exclusive=True) as port:
            c=BenchCable(port,log=lambda s:print(s,flush=True) if s.startswith('  K6: K6 ') else None,timeout=1)
            if not c.handshake(CABLE_HANDSHAKE,wait=4):raise RuntimeError('no Flux baseline handshake')
            try:
                before=struct.unpack('<4I',once(c,0x71))
                if a.baud!=115200:
                    port.write(cps_frame(CMD_BAUD,0,struct.pack('<I',a.baud))+cps_frame(0x70,0,request)+cps_frame(CMD_BAUD,0,struct.pack('<I',115200)))
                    port.flush()
                    ack=c._frame(time.monotonic()+1)
                    if ack is None or ack[:2]!=(CMD_BAUD,0) or len(ack[2])!=4:raise RuntimeError('baud ACK missing')
                    result['actual_baud']=struct.unpack('<I',ack[2])[0]
                    port.baudrate=a.baud
                else:port.write(cps_frame(0x70,0,request))
                deadline=time.monotonic()+a.ms/1000+8
                while time.monotonic()<deadline:
                    f=c._frame(min(deadline,time.monotonic()+3))
                    if f is None:raise RuntimeError('capture stalled')
                    cmd,addr,data=f
                    if cmd==0x69:frames.append((addr,data))
                    elif cmd==0x70:
                        if len(data)!=112:raise ValueError('bad capture statistics length')
                        stats=dict(zip(FIELDS,struct.unpack('<24I',data[:96])))
                        for k in ('status','minimum','maximum','sum'):
                            if stats[k]&0x80000000:stats[k]-=1<<32
                        result['stats']=stats
                        hz,mix,rssi,r3d,r43,route=struct.unpack('<IIHHHH',data[96:])
                        result['rf']=dict(hz=hz,mix_hz=mix,rssi_dbm=((rssi&511)>>1)-174,if_reg=r3d,bandwidth_reg=r43,gp0_route=route)
                        break
                    elif cmd==0xee:raise RuntimeError(f'radio refused capture: {data.hex()}')
                    else:raise RuntimeError(f'unexpected frame {cmd:#x}')
                if 'stats' not in result:raise RuntimeError('capture statistics missing')
                if a.baud!=115200:
                    ack=c._frame(time.monotonic()+2)
                    if ack is None or ack[:2]!=(CMD_BAUD,0):raise RuntimeError('baseline restoration ACK missing')
                    port.baudrate=115200;time.sleep(.08)
                restore=True
                result['response_crc_errors']=c.bad_response_crcs
                after=struct.unpack('<4I',once(c,0x71))
                result['uart_before']=before;result['uart_after']=after
            finally:
                if restore:
                    try:once(c,CMD_END)
                    except (InstallError,OSError):pass
    samples,meta,continuity=decode_frames(frames,result['stats'])
    result['continuity']=continuity
    st=result['stats'];secs=st['ticks']/96000000
    result['capture_seconds']=secs
    result['dma_samples_per_second']=st['produced']/secs if secs else 0
    result['output_samples_per_second']=st['outputs']/secs if secs else 0
    result['maximum_poll_us']=st['max_poll_cycles']/96
    if len(samples):
        z=samples.astype(float)
        if mode:z=z[::2]+1j*z[1::2]
        result['signal']=dict(rms=float(np.sqrt(np.mean(np.abs(z)**2))),mean_real=float(z.real.mean()),mean_imag=float(z.imag.mean()) if mode else 0)
        if not any(continuity[k] for k in ('sequence_gaps','index_gaps','missing_samples')):
            fs=result['output_samples_per_second']
            spectrum=np.abs(np.fft.fft((z-z.mean())*np.hanning(len(z))))**2
            freqs=np.fft.fftfreq(len(z),1/fs)
            valid=np.abs(freqs)>100
            if not mode:valid &= freqs>0
            best=np.argmax(np.where(valid,spectrum,0))
            result['signal']['peak_hz']=float(freqs[best])
    result['passed']=bool(st['status']==0 and not any(st[k] for k in ('overruns','uncertain','tx_drops','dma_errors')) and not any(continuity[k] for k in ('sequence_gaps','index_gaps','nonmonotonic_timestamps','frame_count_mismatch','missing_samples')) and result['response_crc_errors']==0 and result['uart_before']==result['uart_after'] and st['outputs']>0)
    a.output.parent.mkdir(parents=True,exist_ok=True)
    np.savez_compressed(a.output.with_suffix('.npz'),samples=samples,frames=meta)
    a.output.with_suffix('.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result),flush=True)
    return 0 if result['passed'] else 1


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--port',required=True);p.add_argument('--hz',type=int,default=144390000)
    p.add_argument('--baud',type=int,default=500000);p.add_argument('--ms',type=int,default=2000)
    p.add_argument('--ratio',type=int,choices=(1,2,4,8,16,32),default=8)
    p.add_argument('--mix',type=int,default=16927);p.add_argument('--raw',action='store_true')
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    if not 10<=a.ms<=10000 or not 115200<=a.baud<=3000000 or not 0<=a.mix<=40000 or not 50000000<=a.hz<=600000000:p.error('capture parameters out of bounds')
    return capture(a)


if __name__=='__main__':raise SystemExit(main())
