"""Synthetic impairments to offline receiver; never operate a radio."""
import numpy as np
from tools.k6_sotto_fsk_iq_robustness import FS,sweep


def test_noise_only_is_never_sotto_crc_valid():
    n=int(2.4*FS)
    rng=np.random.default_rng(47)
    iq=rng.standard_normal(n)+1j*rng.standard_normal(n)
    x=sweep(iq,noise_ratios=(0,.25),frequency_shifts=(0,))
    assert x['applied_to_saved_capture_only']
    assert all(s['crc_valid']==0 for s in x['scenarios'])


def test_existing_eight_packet_capture_tolerates_small_artificial_offset():
    from pathlib import Path
    from tools.k6_fsk_iq_edges import read_iq
    p=Path('/home/tyler/flux-bench/tx-injection-20261122/fsk10b-l8/fsk10b-l8-run01.npy')
    if not p.exists():return
    x=sweep(read_iq(p,duration=3.0,offset=280500.),
            noise_ratios=(0,.25),frequency_shifts=(-1000.,0.,1000.))
    assert len(x['scenarios'])==6
    assert all(s['has_all_eight'] for s in x['scenarios'])
