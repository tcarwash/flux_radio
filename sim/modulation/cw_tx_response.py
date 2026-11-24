"""Offline measured-IQ TX envelope sweep / response analysis.

Never opens hardware, modifies RFIC registers, keys a transmitter, or suggests
unbounded APC correction. Results from synthetic fixtures remain 'synthetic',
not conducted RF evidence. Only calibrated, AGC-off IQ can support physical
measurements. No attempt is made to infer physical TX gate behavior.
"""
from __future__ import annotations
import math
import numpy as np


def _validated_input(command,iq,rate,frequency_hz):
    u=np.asarray(command,dtype=np.float64)
    z=np.asarray(iq,dtype=np.complex128)
    if (u.ndim!=1 or z.ndim!=1 or len(u)!=len(z) or len(u)<256 or
        not np.isfinite(u).all() or not np.isfinite(z).all() or
        not 0<=u.min()<=u.max()<=1 or
        not math.isfinite(rate) or not 1000<=rate<=10_000_000 or
        not math.isfinite(frequency_hz) or not 0<frequency_hz<rate/10):
        raise ValueError('finite, bounded, co-timed command and complex IQ required')
    return u,z


def _project(real,t,frequency):
    """Complex coherent amplitude: cos coefficient + j sin coefficient."""
    xx=np.column_stack((np.ones(len(t)),np.cos(2*np.pi*frequency*t),
                        np.sin(2*np.pi*frequency*t)))
    beta=np.linalg.lstsq(xx,real,rcond=None)[0]
    pred=xx@beta
    return complex(float(beta[1]),float(beta[2])),float(beta[0]),float(np.sqrt(np.mean((real-pred)**2)))


def analyze_tone(command,iq,rate,frequency_hz,*,trim_cycles=2.,known_rf=False):
    """Coherent AM transfer H(f), carrier-phase ripple, gain and THD proxies.

    Both command and IQ envelope must be synchronized to a common sample
    clock, with fixed SDR gain. RF phase is detrended for carrier offset,
    so reported ripple is *additional* residual AM-to-PM. A measurement
    with uncertain timing/AGC must not be interpreted as a physical filter.
    """
    u,z=_validated_input(command,iq,rate,frequency_hz)
    if not math.isfinite(trim_cycles) or not 0<=trim_cycles<=8:
        raise ValueError('trim_cycles must be finite and in [0,8]')
    nskip=int(math.ceil(trim_cycles*rate/frequency_hz))
    if 2*nskip>len(u)-max(128,int(rate/frequency_hz)*3):
        raise ValueError('capture insufficient for trimmed three-cycle estimation')
    if nskip:
        u=u[nskip:-nskip]
        z=z[nskip:-nskip]
    t=np.arange(len(u),dtype=np.float64)/rate
    cmd,cmd_dc,_=_project(u,t,frequency_hz)
    if abs(cmd)<1e-4:
        raise ValueError('command stimulus has insufficient fundamental variation')
    a=np.abs(z)
    amp,rf_dc,residual=_project(a,t,frequency_hz)
    if rf_dc<1e-6 or not np.isfinite(rf_dc):
        raise ValueError('RF carrier amplitude too small to estimate transfer')
    # Convert amplitude response to *relative modulation depth*, so receive
    # path gain does not affect comparison between fixed-gain IQ captures.
    h=(amp/rf_dc)/(cmd/max(cmd_dc,1e-9))
    if not np.isfinite(h):raise ValueError('invalid response H(f)')
    # Joint harmonic fit prevents spectral leakage in non-integer-duration
    # windows from masquerading as actual PA harmonic distortion.
    harmonic_basis=[np.ones(len(t))]
    for harmonic in (1,2,3):
        harmonic_basis += [np.cos(2*np.pi*frequency_hz*harmonic*t),
                           np.sin(2*np.pi*frequency_hz*harmonic*t)]
    harmonic_fit=np.linalg.lstsq(np.column_stack(harmonic_basis),a,rcond=None)[0]
    fundamentals=[math.hypot(float(harmonic_fit[2*k-1]),float(harmonic_fit[2*k]))
                  for k in (1,2,3)]
    thd=math.hypot(*fundamentals[1:])/max(fundamentals[0],1e-12)
    # Unwrap carrier IQ phase; best-fit linear phase removes CFO. Pure AM
    # causes no extra RF carrier phase ripple. No phase inference at RF off.
    if np.percentile(a,2)<.04*rf_dc:
        raise ValueError('near-off envelope: phase would be unreliable')
    phase=np.unwrap(np.angle(z))
    trend=np.polyfit(t,phase,1)
    remainder=phase-np.polyval(trend,t)
    phase_tone,_,phase_residual=_project(remainder,t,frequency_hz)
    return dict(frequency_hz=float(frequency_hz),
                normalized_am_gain=float(abs(h)),
                lag_degrees=float(math.degrees(np.angle(h))),
                am_depth_command=float(abs(cmd)/max(cmd_dc,1e-9)),
                am_depth_observed=float(abs(amp)/rf_dc),
                envelope_thd_percent=float(100*thd),
                am_pm_ripple_deg=float(math.degrees(abs(phase_tone))),
                rf_carrier_cfo_hz=float(trend[0]/(2*math.pi)),
                rf_amplitude_mean=float(rf_dc),
                envelope_residual_rms=float(residual),
                phase_residual_rms_deg=float(math.degrees(phase_residual)),
                samples_analyzed=len(u),
                physical_rf_evidence=bool(known_rf),
                input_provenance='conducted_IQ_requires_independent_proof' if known_rf
                                 else 'synthetic_model_not_hardware')


def fit_single_pole(points,*,physical=False):
    """Fit 1-pole lowpass gain and cutoff from measured |H(f)| only.

    Residual error is intentionally reported; poor fit is *not* a meaningful
    numerical 'TX bandwidth' claim. Phase/delay are reported separately.
    """
    if not isinstance(points,(list,tuple)) or len(points)<4:
        raise ValueError('at least four frequency response points required')
    f=np.asarray([p['frequency_hz'] for p in points],dtype=float)
    y=np.asarray([p['normalized_am_gain'] for p in points],dtype=float)
    if (not np.isfinite(f).all() or not np.isfinite(y).all() or
        np.any(f<=0) or np.any(y<=0) or
        len(np.unique(f))!=len(f)):
        raise ValueError('need finite positive distinct frequencies and nonzero gain')
    from scipy.optimize import least_squares
    order=np.argsort(f)
    f=f[order]; y=y[order]
    logy=np.log(y)
    def residual(p):
        gain,fc=p
        return gain-.5*np.log1p((f/np.exp(fc))**2)-logy
    solution=least_squares(residual,(float(logy[0]),math.log(float(np.median(f)))),
                           bounds=([-20,math.log(.2)],[20,math.log(100000.)]),
                           max_nfev=180)
    gain=float(math.exp(solution.x[0]));fc=float(math.exp(solution.x[1]))
    resid_db=20/math.log(10)*np.asarray(residual(solution.x))
    rms=float(np.sqrt(np.mean(resid_db*resid_db)))
    return dict(model='single_pole_amplitude_only_schematic',
                estimated_cutoff_hz=fc,
                low_frequency_normalized_gain=gain,
                response_fit_rms_db=rms,
                fit_credible=bool(solution.success and rms<2.0 and
                                    min(f)<fc and max(f)>fc),
                points=len(points),physical_rf_evidence=bool(physical),
                warning=('Poor single-pole fit: use measured points, not fitted cutoff'
                         if rms>=2.0 else
                         'Cutoff inference is limited by sweep span/transfer shape'))


def synth_tone(frequency_hz,*,rate=48000,duration_s=None,cutoff_hz=90.,
               rf_am_pm_deg=0.,cfo_hz=230.,depth=.25,seed=7,snr_db=50):
    """Idealized fixed-gain coherent AM test fixture; NEVER hardware data."""
    if not math.isfinite(frequency_hz) or frequency_hz<=0 or frequency_hz>rate/8:
        raise ValueError('invalid stimulus tone')
    if (not math.isfinite(cutoff_hz) or cutoff_hz<=0 or
        not math.isfinite(depth) or not 0<depth<=.35 or
        not math.isfinite(rf_am_pm_deg) or abs(rf_am_pm_deg)>90 or
        not math.isfinite(snr_db) or not 0<=snr_db<=120):
        raise ValueError('invalid synthetic TX transfer response')
    if duration_s is None:
        duration_s=max(.12,10/frequency_hz)
    if not math.isfinite(duration_s) or duration_s<=0 or duration_s>15:
        raise ValueError('invalid bounded duration')
    size=int(math.ceil(rate*duration_s))
    if size<256 or size>600000:raise ValueError('bounded synthetic waveform length required')
    t=np.arange(size,dtype=float)/rate
    command=.55+depth*np.cos(2*np.pi*frequency_hz*t)
    alpha=-math.expm1(-2*math.pi*cutoff_hz/rate)
    analog=np.empty_like(command)
    state=.55
    for i,v in enumerate(command):
        state+=alpha*(v-state)
        analog[i]=state
    pm=math.radians(rf_am_pm_deg)*(analog-.55)
    x=analog*np.exp(1j*(2*np.pi*cfo_hz*t+pm))
    rng=np.random.default_rng(seed)
    noise=10**(-snr_db/20)*.55/math.sqrt(2)
    x+=noise*(rng.standard_normal(size)+1j*rng.standard_normal(size))
    return command,x.astype(np.complex64)


def bounded_lowpass_preemphasis(target,*,rate=48000.,cutoff_hz=90.,strength=1.,ceiling=1.):
    """Offline clipped inverse of assumed one-pole *RF amplitude* response.

    The signal is a **normalized hypothetical command**, NOT a register
    waveform or physical PA drive authorization. It is a noncausal-free
    feedforward inverse based on the desired ramp samples and a conjectured
    transfer function, with hard upper/lower bounds. The real TX PA can be
    nonlinear, hysteretic, chirpy or fail to key off; this does not solve it.
    """
    y=np.asarray(target,dtype=float)
    if (y.ndim!=1 or len(y)<16 or len(y)>1_000_000 or
        not np.isfinite(y).all() or np.any(y<0) or np.any(y>1) or
        not math.isfinite(rate) or not 1000<=rate<=384000 or
        not math.isfinite(cutoff_hz) or not 1<=cutoff_hz<=5000 or
        not math.isfinite(strength) or not 0<=strength<=1 or
        not math.isfinite(ceiling) or not 0<ceiling<=1):
        raise ValueError('bounded normalized CW target and assumed one-pole required')
    alpha=-math.expm1(-2*math.pi*cutoff_hz/rate)
    ideal=(y-(1-alpha)*np.r_[0.,y[:-1]])/alpha
    command=y+strength*(ideal-y)
    command=np.clip(command,0,ceiling)
    # Target samples already occupy the scheduled CW key interval; no
    # intended RF output before it. Hard clamp all requested key-up samples.
    command[y<=0]=0.
    return command
