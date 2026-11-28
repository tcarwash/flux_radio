#!/usr/bin/env python3
"""Held-out Sotto screening of gentler fixed FIR GP0 noise masks."""
from __future__ import annotations
import argparse, json, sys, tempfile
from pathlib import Path
import numpy as np
from scipy.signal import firwin2, lfilter
ROOT=Path(__file__).resolve().parents[1]
RFSIM=Path('/storage5/dev/rfsim-k6-gp0-20261127')
sys.path[:0]=[str(ROOT),str(RFSIM)]
import types
pkg=sys.modules.get('tools')
if pkg is None or str(ROOT/'tools') not in getattr(pkg,'__path__',[]):
    pkg=types.ModuleType('tools');pkg.__path__=[str(ROOT/'tools')];sys.modules['tools']=pkg
from tools.k6_gp0_fir_wiener_study import (
    FS,NFFT,TRAIN,TEST,SEEDS,VPP,TARGET_SNR_DB,source_pcm,average_psd,
    align_causal,
)
from tools.k6_gp0_causal_denoise_sotto_v38 import causal_denoise
from tools.k6_gp0_noise_sotto_v38_study import (
    SAMPLE_ROOT,build_codec,encode_decode,read_flac,resample_poly,score,
)
from rfsim.k6_mic import OFF_PCM_RMS_FS,generate_gp0_noise

SCALES=(1.5,)
FLOORS=(0.10,)
TAPS=(15,)

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out-dir',type=Path,required=True)
    a=ap.parse_args();out=a.out_dir.resolve()
    if out.exists():ap.error(f'refusing overwrite: {out}')
    out.mkdir(parents=True)
    train=[source_pcm(v)[1] for v in TRAIN]
    freq,ps=average_psd(train)
    noise_unit=generate_gp0_noise(FS*120,seed=90817,model='banded',rms_fs=1.0)
    from scipy.signal import welch
    pn=welch(noise_unit,fs=FS,nperseg=NFFT,noverlap=NFFT//2,detrend=False,scaling='spectrum')[1]
    srms=float(np.mean([np.mean(x*x) for x in train]))**0.5
    nrms=srms/(10**(TARGET_SNR_DB/20));pn*=nrms*nrms/float(np.mean(noise_unit*noise_unit))
    coeff={}
    for scale in SCALES:
        for floor in FLOORS:
            mix=ps+pn
            gain=np.maximum((mix-scale*pn)/np.maximum(mix,1e-20),floor)
            gain=np.clip(gain,floor,1.0)
            for n in TAPS:
                h=firwin2(n,freq/(FS/2),gain,window=('kaiser',5.0),fs=2)
                key=f's{scale:g}_f{floor:g}_n{n}'
                q=np.clip(np.rint(h*32768),-32768,32767).astype(np.int16)
                coeff[key]=q.astype(np.float64)/32768
    report={'schema':1,'offline_only':True,'experiment':'held-out 15-tap fixed FIR candidate screen through real Sotto v3.8',
            'source_model':'RFSim GP0 measured-response source plus banded noise','train_voices':list(TRAIN),
            'heldout_voices':list(TEST),'seeds':list(SEEDS),'input_vpp':VPP,
            'target_snr_db':TARGET_SNR_DB,'taps':list(TAPS),'scales':list(SCALES),'floors':list(FLOORS),
            'limitations':['statistical noise model, not a long physical GP0 silence capture','no MCU cycle/RAM or radio test'],
            'heldout':{}}
    with tempfile.TemporaryDirectory(prefix='gp0-fir-wiener-sweep-') as td:
        temp=Path(td);enc,dec=build_codec(temp)
        for voice in TEST:
            path=SAMPLE_ROOT/f'librispeech_{voice}__original16.flac'
            ref=resample_poly(read_flac(path,16000),1,2)
            x=ref/max(float(np.max(np.abs(ref))),1e-12)
            from rfsim.k6_mic import k6_gp0_pcm
            cleanpcm=k6_gp0_pcm(x,input_vpp=VPP,noise_rms_fs=0,response='measured',clip=True)
            clean_dec=encode_decode(enc,dec,cleanpcm,temp,f'{voice}-clean')
            target=float(np.std(clean_dec));rows=[]
            for seed in SEEDS:
                noise=generate_gp0_noise(len(cleanpcm),seed=seed,model='banded',rms_fs=OFF_PCM_RMS_FS)
                noise*=float(np.std(cleanpcm))/(10**(TARGET_SNR_DB/20)*float(np.std(noise)))
                mix=cleanpcm+noise;np.clip(mix,-1,1,out=mix)
                pre=generate_gp0_noise(FS*2,seed=seed+1_000_003,model='banded',rms_fs=float(np.std(noise)))
                trials={'noisy':mix,
                        'stft_wiener':causal_denoise(mix,pre,'wiener',noise_scale=2.5,mask_floor=.03)}
                for key,h in coeff.items():
                    y=align_causal(lfilter(h,[1.0],mix),15)
                    trials['fir_'+key]=y
                item={'seed':seed,'scores':{}}
                for name,y in trials.items():
                    decoded=encode_decode(enc,dec,y,temp,f'{voice}-{seed}-{name}')
                    item['scores'][name]=score(ref,decoded,target)
                rows.append(item)
            report['heldout'][voice]=rows
            print(f'{voice}: {len(rows)} seeds complete',flush=True)
    names=['noisy','stft_wiener']+['fir_'+k for k in coeff]
    report['summary']={}
    for name in names:
        vals=[r['scores'][name] for rows in report['heldout'].values() for r in rows]
        report['summary'][name]={'mean_estoi':round(float(np.mean([x['estoi'] for x in vals])),5),
                                 'mean_pesq_nb':round(float(np.mean([x['pesq_nb'] for x in vals])),4),
                                 'case_count':len(vals)}
    (out/'report.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
    best=max(report['summary'],key=lambda k:report['summary'][k]['mean_estoi'] if k.startswith('fir_') else -1)
    key=best.removeprefix('fir_')
    q=np.clip(np.rint(coeff[key]*32768),-32768,32767).astype(np.int16)
    (out/'selected_fir_q15.h').write_text('/* Offline selected candidate; do not treat as RF-qualified. */\n#pragma once\n#include <stdint.h>\nstatic const int16_t gp0_wiener_q15[31] = {\n  '+', '.join(map(str,q.tolist()))+'\n};\n')
    print(json.dumps({'best_by_heldout_estoi':best,'summary':report['summary']},indent=2))

if __name__=='__main__':main()
