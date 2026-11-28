"""Pilot/sync based offline FM burst acquisition with unknown leading samples.

Acquire on SoapyFang discriminator IQ. Only *public* pilot and sync symbols,
frame format and expected payload length are known. Payload truth is not used
for timing, gain, offset, phase, sign or level decisions.

This is a fixed-symbol-clock reference receiver, not a hardware chip emulator
or production AGC/CFO/clock-recovery loop. The native-FSK FIFO model already
contains its own separate preamble+sync acquisition logic.
"""
from __future__ import annotations
import math
import numpy as np


def acquire(discriminator_hz,*,baud,rate,order,pilot,sync,payload_symbols,
            min_level_span_hz=250.,min_correlation=.68):
    """Return blind-start acquired symbol stream, never a truth-aided decision.

    Searches all sps sample phases and all possible full-frame locations in
    the discriminator. The public sync validates the pilot correlation; its
    bits are *not* used to fit RF levels or sample phase. The scoring of a
    held-out payload must happen in the caller, after `acquire` returns.
    """
    rx=np.asarray(discriminator_hz,dtype=float)
    public_pilot=np.asarray(pilot,dtype=np.int8)
    public_sync=np.asarray(sync,dtype=np.int8)
    if (rx.ndim!=1 or not len(rx) or not np.isfinite(rx).all() or
        not isinstance(rate,int) or not isinstance(baud,int) or
        rate<=0 or baud<=0 or rate%baud or
        order not in (2,4) or not 0<min_correlation<1 or
        min_level_span_hz<=0 or not math.isfinite(min_level_span_hz) or
        not isinstance(payload_symbols,int) or not 8<=payload_symbols<=1024 or
        len(public_pilot)<16 or len(public_sync)<8 or
        any(np.any((x<0)|(x>=order)) for x in (public_pilot,public_sync)) or
        len(set(public_pilot.tolist()))!=order):
        raise ValueError('invalid public sync / discriminator / symbol-clock parameters')
    sps=rate//baud
    pre=len(public_pilot)
    ns=len(public_sync)
    frame_symbols=pre+ns+payload_symbols
    if len(rx)<frame_symbols*sps:
        raise ValueError('short recording for configured fixed-size frame')
    half=max(1,int(sps*.18))
    if 2*half>=sps:half=max(0,(sps-1)//2)
    # Prefix integral avoids per-symbol slicing while searching every phase.
    cs=np.r_[0.,np.cumsum(rx)]
    templ=public_pilot.astype(float)
    templ-=templ.mean()
    tmpl_energy=float(np.dot(templ,templ))
    if tmpl_energy<=0:raise ValueError('noninformative preamble')
    best=None
    considered=0
    correlated=0
    verified_sync=0
    for phase in range(sps):
        centers=np.arange(phase+sps//2,len(rx)-half,sps,dtype=int)
        centers=centers[centers>=half]
        if len(centers)<frame_symbols:continue
        starts=centers-half
        ends=centers+half+1
        v=(cs[ends]-cs[starts])/(2*half+1)
        # m-symbol correlation and rolling variance, independent of payload.
        sums=np.r_[0.,np.cumsum(v)]
        squares=np.r_[0.,np.cumsum(v*v)]
        win_mean=sums[pre:]-sums[:-pre]
        var=np.maximum(squares[pre:]-squares[:-pre] - win_mean*win_mean/pre,1e-12)
        score=np.correlate(v,templ,'valid')/np.sqrt(var*tmpl_energy)
        limit=len(v)-frame_symbols+1
        considered+=max(0,limit)
        for index in np.flatnonzero(np.abs(score[:limit])>=min_correlation):
            correlated+=1
            training=v[index:index+pre]
            means=np.array([np.mean(training[public_pilot==k]) for k in range(order)])
            if float(np.ptp(means))<min_level_span_hz:
                continue
            observed=v[index:index+frame_symbols]
            decisions=np.argmin(abs(observed[:,None]-means[None,:]),axis=1).astype(np.int8)
            if not np.array_equal(decisions[pre:pre+ns],public_sync):
                continue
            pilot_errors=int(np.count_nonzero(decisions[:pre]!=public_pilot))
            if pilot_errors>max(1,len(public_pilot)//16):
                continue
            verified_sync+=1
            # Valid acquisition requires *public* sync and pilot. No payload
            # bits or quality metrics influence selection.
            quality=(pilot_errors,-abs(float(score[index])),int(centers[index])-sps//2)
            if best is None or quality<best[0]:
                best=(quality,dict(decisions=decisions,estimated_levels_hz=means,
                                   pilot_errors=pilot_errors,sync_errors=0,
                                   start_sample=int(centers[index]-sps//2),
                                   sample_phase=int(phase),
                                   pilot_correlation=float(abs(score[index])),
                                   level_order_valid=bool(np.all(np.diff(means)>0)),
                                   estimated_center_offset_hz=float(np.mean(means)),
                                   acquisition='blind_start_public_pilot_plus_sync',
                                   samples_per_symbol=sps))
    if best is None:
        return dict(locked=False,candidates_considered=considered,
                    correlated_candidates=correlated,sync_validated_candidates=0,
                    acquisition='blind_start_public_pilot_plus_sync')
    result=best[1]
    result.update(locked=True,candidates_considered=considered,
                  correlated_candidates=correlated,
                  sync_validated_candidates=verified_sync)
    return result
