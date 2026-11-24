"""Synthetic, strictly bounded AM driver register intentions for K6 research.

No physical SPI, PA, radio serial, API or transmitter is accessed. The
REG36 low six bits and REG30 gate are mathematical hypotheses ONLY. This
module deliberately limits gain updates to a user-declared ceiling; it does
not authorize or create a physical open-loop driver API.
"""
from __future__ import annotations
import math
import numpy as np
from .tx_trace_replay import ReplayAssumptions


def schedule_driver_am(envelope, *, gain_ceiling=0x22, write_us=117.,
                       rate=48000, clock_hz=96_000_000, gate_mask=0x8):
    """Prepare gain -> enable hypothetical gate -> causal 6-bit AM updates.

    `envelope` is a 0..1 target relative to the declared maximum allowed
    driver code, not a physical RF-voltage calibration. At most one command
    per 3-wire transaction, including setup and off. Existing gain state is
    never inferred. A finite waveform is always followed by gate disable.
    """
    a=np.asarray(envelope,dtype=float)
    if a.ndim!=1 or not 16<=len(a)<=1_000_000 or not np.isfinite(a).all() or np.any(a<0) or np.any(a>1):
        raise ValueError('nonempty bounded 0..1 envelope of at least 16 samples required')
    if not isinstance(gain_ceiling,int) or not 1<=gain_ceiling<=0x22:
        raise ValueError('gain ceiling cannot exceed hypothetical low-power 0x22')
    if not math.isfinite(write_us) or not 0<write_us<=1e6:
        raise ValueError('write interval must be positive finite')
    ReplayAssumptions(clock_hz=clock_hz,sample_rate=rate,gate_mask=gate_mask).validate()
    period=max(1,math.ceil(rate*write_us*1e-6))
    # Optional early writes are not allowed: two setup transactions precede
    # key-up. In the report the first few samples are separately flagged.
    initial_code=min(gain_ceiling,int(round(a[0]*gain_ceiling)))
    writes=[(0,0x30,0)]
    writes.append((math.ceil(period*clock_hz/rate),0x36,initial_code))
    writes.append((math.ceil(2*period*clock_hz/rate),0x30,gate_mask))
    previous=initial_code
    for n in range(3*period,len(a),period):
        code=min(gain_ceiling,int(round(float(a[n])*gain_ceiling)))
        if code!=previous:
            writes.append((math.ceil(n*clock_hz/rate),0x36,code))
            previous=code
    stop_tick=math.ceil(len(a)/period)*period
    writes.append((math.ceil(stop_tick*clock_hz/rate),0x30,0))
    # Same-register values in high bits are not presumed retained; report
    # only schematic REG36 driver codes (not a safe full hardware register).
    assert all(reg!=0x36 or 0<=val<=gain_ceiling for _,reg,val in writes)
    assert all((b[0]-a[0])*rate/clock_hz>=period-1e-9 for a,b in zip(writes,writes[1:]))
    return writes


def percent_modulation(envelope):
    a=np.asarray(envelope,dtype=float)
    if a.ndim!=1 or len(a)<16 or not np.isfinite(a).all() or np.any(a<0):
        raise ValueError('finite nonnegative envelope required')
    hi=float(np.percentile(a,99.5));lo=float(np.percentile(a,.5))
    return (hi-lo)/(hi+lo) if hi+lo else 0.


def tone_quality(envelope, *, rate=48000,tone_hz=1000.,trim_ms=25.):
    """Measure normalized AM-envelope tone THD via coherent projection.

    Reports distortion of *envelope only*, not PA intermodulation products,
    occupied spectrum, AM-PM or a certified RF bandwidth. Analysis excludes
    startup and tail; caller must separately check physical gate-off tail.
    """
    y=np.asarray(envelope,dtype=float)
    if y.ndim!=1 or len(y)<100 or not np.isfinite(y).all() or np.any(y<0):
        raise ValueError('finite, nonnegative envelope required')
    if not math.isfinite(rate) or rate<=0 or not math.isfinite(tone_hz) or not 0<tone_hz<rate/8:
        raise ValueError('invalid coherent-tone clock')
    if not math.isfinite(trim_ms) or not 0<=trim_ms<250:
        raise ValueError('invalid trim time')
    skip=int(round(rate*trim_ms/1000))
    if 2*skip>=len(y)-100:raise ValueError('insufficient stationary tone window')
    view=y[skip:len(y)-skip if skip else None]
    t=np.arange(len(view),dtype=float)/rate
    # Least-squares independent sin/cos projection resolves tone components
    # without requiring a coherent FFT or inventing signal power after trim.
    dc=np.mean(view)
    audio=view-dc
    def magnitude(freq):
        phase=2*np.pi*freq*t
        basis=np.column_stack((np.cos(phase),np.sin(phase)))
        coeff=np.linalg.lstsq(basis,audio,rcond=None)[0]
        return float(np.hypot(*coeff))
    fundamental=magnitude(tone_hz)
    harmonics=[magnitude(k*tone_hz) for k in (2,3,4)]
    thd=math.sqrt(sum(x*x for x in harmonics))/max(fundamental,1e-15)
    fitted=np.linalg.lstsq(np.column_stack((np.ones(len(view)),np.cos(2*np.pi*tone_hz*t),
                                              np.sin(2*np.pi*tone_hz*t))),view,rcond=None)[0]
    predicted=fitted[0]+fitted[1]*np.cos(2*np.pi*tone_hz*t)+fitted[2]*np.sin(2*np.pi*tone_hz*t)
    return dict(depth=percent_modulation(view),fundamental=float(fundamental),
                thd_percent=float(100*thd),
                normalized_residual_rms=float(np.sqrt(np.mean((view-predicted)**2))/max(fundamental,1e-12)),
                sample_count=len(view),mean=float(dc),
                harmonics=[float(v) for v in harmonics])


def spectral_quality(iq, *, rate=48000,tone_hz=1000.,trim_ms=25.):
    """Coherent carrier/USB/LSB and phase-error metrics for an AM tone.

    This is *baseband complex IQ* from a virtual receiver, never conducted RF
    spectrum. Carrier phase error / upper/lower sideband imbalance can reveal
    hypothetical AM-to-PM coupling that envelope THD alone cannot detect.
    """
    z=np.asarray(iq,dtype=np.complex128)
    if z.ndim!=1 or len(z)<100 or not np.isfinite(z).all():
        raise ValueError('finite complex 1-D IQ required')
    if not math.isfinite(rate) or rate<=0 or not math.isfinite(tone_hz) or not 0<tone_hz<rate/8:
        raise ValueError('invalid coherent spectral clock')
    if not math.isfinite(trim_ms) or not 0<=trim_ms<250:
        raise ValueError('invalid trim window')
    trim=int(round(rate*trim_ms/1000))
    if 2*trim>len(z)-100:raise ValueError('insufficient IQ for spectral analysis')
    x=z[trim:len(z)-trim if trim else None]
    # Coherent reference; the AM test is a 1-kHz tone over 200 ms after trim.
    t=np.arange(len(x))/rate
    carrier=complex(np.mean(x))
    upper=complex(np.mean(x*np.exp(-2j*np.pi*tone_hz*t)))
    lower=complex(np.mean(x*np.exp(2j*np.pi*tone_hz*t)))
    power_up=abs(upper)**2; power_down=abs(lower)**2
    asymmetry=10*math.log10((power_up+1e-20)/(power_down+1e-20))
    # Average carrier phase is removed: pure AM has no time-varying phase.
    phase=np.unwrap(np.angle(x))
    phase-=np.mean(phase)
    rms=math.degrees(float(np.sqrt(np.mean(phase**2))))
    return dict(sideband_asymmetry_db=float(asymmetry),phase_rms_degrees=float(rms),
                carrier_magnitude=float(abs(carrier)),
                upper_sideband_magnitude=float(abs(upper)),
                lower_sideband_magnitude=float(abs(lower)))
