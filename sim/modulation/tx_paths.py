"""Offline-only K6-B transmit-path hypotheses and polar impairment models.

NO hardware access, register writes, mode installation, or transmission.
Evidence status describes *individual hardware primitives* only; composing
multiple individually observed primitives does not establish their coexistence.
Physical transfer functions here are explicit assumptions, not calibrations.
"""
from __future__ import annotations
from dataclasses import dataclass
from itertools import combinations
import math
import numpy as np

SAMPLE_RATE=48_000

@dataclass(frozen=True)
class Path:
    evidence: str
    note: str

ENVELOPES={
    'ideal': Path('reference_only','Unconstrained mathematical IQ amplitude'),
    'constant_pa': Path('observed_individual','Factory-low steady carrier; no amplitude tracking'),
    'hard_gate': Path('observed_individual','Hard PA cutoff; RF tail/click risk'),
    'measured_gate': Path('observed_individual_model','VHF-low CW measured ~5.25 ms rise and 0.765 ms fall'),
    'apc_slow': Path('observed_individual_model','External APC envelope dynamics ~48-91 Hz, simplified LPF'),
    'apc_knee': Path('observed_pathology_model','APC intermediate-target ~90-95 Hz oscillation; approximate'),
    'driver6': Path('unverified_hardware','REG36 6-bit open-loop PA driver, dynamic control unverified'),
    'bias4': Path('unverified_hardware','REG19 4-bit bias DAC, dynamic control unverified'),
    'driver6_bias4': Path('unverified_combination','Optimistic 10-bit envelope resolution; simultaneous control unproven'),
    'driver6_gate': Path('unverified_combination','Optimistic open-loop drive ramp plus final hard PA cutoff'),
}
ACTUATORS={
    'carrier': Path('observed_individual','Unmodulated carrier; no controlled phase trajectory'),
    'native_fsk': Path('observed_individual','Native FD6818B FIFO TX binary FM at 4800 baud'),
    'tone': Path('observed_individual','REG70/71 TX single audio tone frequency-modulates carrier'),
    'dual_tone': Path('unverified_hardware','REG72 second DTMF oscillator on B-chip not physically proved'),
    'subaudio': Path('observed_individual_model','REG51 subaudio binary/DC slow FM; ~315 sym/s evidence'),
    'dcs_bits': Path('observed_individual_model','Digital coded squelch bitstream via subaudio FM; symbol duration modeled'),
    'ctcss_phase': Path('unverified_hardware','REG52 CTCSS tone-phase shifter, NOT direct RF QPSK phase control'),
    'micin': Path('observed_individual_model','Audio into MICIN FM with passband/limiter assumptions'),
    'gp0': Path('unverified_hardware','External GP0 CDCSS TX route unproven on B-chip'),
    'signed_dac': Path('unverified_hardware','TX27 signed 8-bit deviation DAC combination unproven'),
    'reg40': Path('unverified_control','Dynamic REG40 deviation scale only meaningful with native FSK'),
}

SHARED_MUX_CONFLICTS=(
    frozenset(('native_fsk','dual_tone')), # REG72 FSK clock vs DTMF oscillator
    frozenset(('native_fsk','tone')), # REG70 deviation vs oscillator setup conflict unknown
    frozenset(('subaudio','signed_dac')), # REG51 encodings conflict
    frozenset(('subaudio','gp0')), # REG51 source-select conflict
    frozenset(('subaudio','dcs_bits')), # digital and analog subaudio mux conflict
    frozenset(('subaudio','ctcss_phase')), # same subaudio source ownership unverified
    frozenset(('signed_dac','dcs_bits')), # REG51 source encoding conflict
    frozenset(('gp0','dcs_bits')), # external vs internal DCS path conflict
    frozenset(('ctcss_phase','dcs_bits')), # subaudio voice modulation mux conflict
    frozenset(('dual_tone','tone')), # duplicated tone path
    frozenset(('native_fsk','gp0')), # conflicting modem TX sources unproven
)


def compatibility(actors):
    """Return status, explanation, without implying any RF coexistence."""
    a=tuple(sorted(set(actors)))
    if a==('ideal',):
        return 'reference_only','Ideal arbitrary phase trajectory (not an RF register path)'
    if 'ideal' in a:
        return 'conflict','Ideal IQ is not a hardware actuator; do not combine with registers'
    if not a or len(a)>3 or len(a)!=len(tuple(actors)):
        return 'conflict','Path must contain 1–3 distinct actuators'
    if any(x not in ACTUATORS for x in a):
        return 'conflict','Unknown source'
    if 'reg40' in a and 'native_fsk' not in a:
        return 'conflict','REG40 is deviation control only with native FSK here'
    for pair in SHARED_MUX_CONFLICTS:
        if pair.issubset(a):
            return 'conflict','Known/shared TX mode/register mux contention; independent source coexistence unproved'
    if len(a)==1:
        entry=ACTUATORS[a[0]]
        return entry.evidence,entry.note
    return 'unverified_combination','Concurrent actuators or control behavior require independent B-chip validation'


def all_configurations(max_actors=3):
    """Enumerate all source subsets + every envelope; preserve conflicts."""
    if max_actors not in (1,2,3):raise ValueError('max_actors must be 1..3')
    out=[]
    for n in range(1,max_actors+1):
        for actors in combinations(ACTUATORS,n):
            status,reason=compatibility(actors)
            for env in ENVELOPES:
                out.append(dict(actors=list(actors),envelope=env,compatibility=status,
                                explanation=reason,amplitude_evidence=ENVELOPES[env].evidence,
                                actor_evidence=[ACTUATORS[x].evidence for x in actors],
                                hardware_qualified=False))
    return out


def lowpass(samples, hz, rate):
    x=np.asarray(samples,dtype=np.float64)
    if hz<=0:raise ValueError('hz must be positive')
    alpha=1-math.exp(-2*math.pi*hz/rate)
    out=np.empty_like(x)
    prior=0.
    for i,z in enumerate(x):
        prior+=alpha*(z-prior)
        out[i]=prior
    return out


def spi_sample_hold(x, *, write_us=117., rate=SAMPLE_RATE, writes=1):
    """Optimistic zero-jitter register updates, limited by serial write time.

    Default ~117us is the Aura software 3-wire timing model, not a measured
    analog TX settle time. `write_us=5` is an *unverified fast-SPI scenario*.
    """
    if not math.isfinite(write_us) or write_us<0 or write_us>1e6:
        raise ValueError('write_us must be finite, in 0..1e6')
    if writes not in (1,2):raise ValueError('writes must be 1 or 2')
    z=np.asarray(x)
    if z.ndim!=1:raise ValueError('1D input required')
    hold=max(1,math.ceil(rate*write_us*1e-6*writes))
    return np.repeat(z[::hold],hold)[:len(z)]

def envelope_response(target, kind,rate=SAMPLE_RATE,spi_write_us=117.):
    a=np.asarray(target,dtype=np.float64)
    if a.ndim!=1 or not np.all(np.isfinite(a)) or (a<0).any():
        raise ValueError('Envelope must be a finite nonnegative vector')
    if kind not in ENVELOPES:raise ValueError('Unknown envelope actuator')
    if kind=='ideal':return a.copy()
    if kind=='constant_pa':return np.ones_like(a)
    if kind=='hard_gate':return np.where(a>=.45,1.,0.)
    if kind=='measured_gate':
        target=(a>=.45).astype(float)
        z=np.empty_like(target);v=0.
        for i,t in enumerate(target):
            tau=.00525 if t>v else .000765
            v+=(1-math.exp(-1/(rate*tau)))*(t-v)
            z[i]=v
        return z
    if kind=='apc_slow':return np.clip(lowpass(a,65.,rate),0,1)
    if kind=='apc_knee':
        # This oscillatory response is intentionally schematic, not fitted IQ.
        t=np.arange(len(a))/rate
        return np.clip(lowpass(a,65.,rate)*(1+.38*np.sin(2*math.pi*93*t)),0,1)
    if kind=='bias4':
        return spi_sample_hold(np.round(lowpass(np.clip(a,0,1),350,rate)*15)/15,
                               write_us=spi_write_us,rate=rate)
    if kind=='driver6':
        return spi_sample_hold(np.round(lowpass(np.clip(a,0,1),4000,rate)*63)/63,
                               write_us=spi_write_us,rate=rate)
    if kind=='driver6_bias4':
        return spi_sample_hold(np.round(lowpass(np.clip(a,0,1),4000,rate)*1023)/1023,
                               write_us=spi_write_us,rate=rate,writes=2)
    if kind=='driver6_gate':
        slow=np.round(lowpass(np.clip(a,0,1),4000,rate)*63)/63
        return np.where(a>.03,spi_sample_hold(slow,write_us=spi_write_us,rate=rate),0.)
    raise AssertionError(kind)


def deviation_components(source, actors,rate=SAMPLE_RATE,spi_write_us=117.):
    """Optimistic target-derivative projection into available FM actuators.

    The source contains the *desired* host complex IQ. No RF synthesizer or
    data path exists here; components do not claim reachable register states.
    """
    z=np.asarray(source,dtype=np.complex128)
    if z.ndim!=1 or not len(z) or not np.all(np.isfinite(z)):
        raise ValueError('finite complex 1D waveform required')
    if rate<=0:raise ValueError('rate must be positive')
    phase=np.unwrap(np.angle(z))
    df=np.diff(phase,prepend=phase[0])*rate/(2*math.pi)
    df=np.clip(df,-rate*.45,rate*.45)
    result=np.zeros(len(df))
    if actors==('ideal',):return df
    status,reason=compatibility(actors)
    if status=='conflict':raise ValueError(reason)
    components=[]
    actors=set(actors)
    if 'carrier' in actors:components.append(np.zeros_like(df))
    if 'native_fsk' in actors:
        spb=int(round(rate/4800))
        deviation=1500.
        blk=np.repeat(np.where(np.array([np.mean(df[i:i+spb]) for i in range(0,len(df),spb)])>=0,1.,-1.),spb)[:len(df)]
        components.append((1250. if 'reg40' in actors else deviation)*blk)
    if 'tone' in actors or 'dual_tone' in actors:
        t=np.arange(len(df))/rate
        frequencies=[1000.] if 'tone' in actors else [700.,1200.]
        val=np.zeros_like(df)
        for f in frequencies:
            wave=np.sin(2*math.pi*f*t)
            strength=np.dot(wave,df)/max(float(np.dot(wave,wave)),1e-12)
            val+=max(-2000,min(2000,strength))*wave
        components.append(spi_sample_hold(val,write_us=spi_write_us,rate=rate))
    if 'subaudio' in actors:
        val=np.round(np.clip(lowpass(df,135,rate),-800,800)/12.5)*12.5
        components.append(spi_sample_hold(val,write_us=spi_write_us,rate=rate))
    if 'dcs_bits' in actors:
        spb=int(round(rate/134.4))
        symbols=np.array([1. if np.mean(df[i:i+spb])>=0 else -1.
                          for i in range(0,len(df),spb)])
        components.append(300.*np.repeat(symbols,spb)[:len(df)])
    if 'ctcss_phase' in actors:
        # REG52 changes the phase of a low-frequency CTCSS oscillator;
        # it does *not* directly shift the RF carrier by 120/180/240 deg.
        t=np.arange(len(df))/rate
        phase_step=(2*np.pi/3)*int(np.clip(np.round(np.mean(df)/200),0,2))
        components.append(200*np.sin(2*np.pi*123*t+phase_step))
    if 'micin' in actors:
        lp=lowpass(df,2700,rate)
        hp=lp-lowpass(lp,300,rate)
        components.append(np.clip(hp,-3000,3000))
    if 'gp0' in actors:components.append(1500*np.where(df>=0,1.,-1.))
    if 'signed_dac' in actors:
        val=np.round(np.clip(lowpass(df,3500,rate),-5000,5000)/39.0625)*39.0625
        components.append(spi_sample_hold(val,write_us=spi_write_us,rate=rate))
    if 'reg40' in actors and 'native_fsk' not in actors:raise ValueError('REG40 requires native FSK')
    # For unknown simultaneous connections assume simple additive FM; mark only
    # an optimistic mathematical possibility, never physical coexistence.
    if components:
        for x in components:result+=x
    return np.clip(result,-rate*.45,rate*.45)


def synthesize(source,actors=('carrier',),envelope='constant_pa',rate=SAMPLE_RATE,spi_write_us=117.):
    """Return deterministic complex IQ after two hypothesized TX axes."""
    z=np.asarray(source,dtype=np.complex128)
    if z.ndim!=1 or len(z)==0 or not np.all(np.isfinite(z)):
        raise ValueError('finite nonempty complex 1D signal required')
    if envelope not in ENVELOPES:raise ValueError('Unknown envelope')
    a=envelope_response(np.abs(z),envelope,rate,spi_write_us)
    if tuple(actors)==('ideal',):
        f=np.diff(np.unwrap(np.angle(z)),prepend=np.angle(z[0]))*rate/(2*math.pi)
    else:f=deviation_components(z,tuple(actors),rate,spi_write_us)
    ph=np.angle(z[0]) + 2*math.pi*np.cumsum(f)/rate
    out=a*np.exp(1j*ph)
    if not np.all(np.isfinite(out)):raise AssertionError('Invalid polar waveform')
    return out.astype(np.complex64)
