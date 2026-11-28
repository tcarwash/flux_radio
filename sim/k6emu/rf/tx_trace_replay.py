"""Causal *offline-only* envelope replay from emulated K6 FD6818B writes.

This is not an FD6818B amplitude transfer-function calibration or a TX
backend. It never accesses the actual radio. The operator MUST declare a
hypothetical TX gate bit; none is inferred from REG30 automatically (Aura's
RX/TX and microphone bits are not proof of an RF PA switch). No gate evidence
means no RF output in the replay. Physical APC, driver and PA behavior are
unverified and only an optional schematic response can be selected.
"""
from __future__ import annotations
from dataclasses import dataclass
import math
import numpy as np
from .tx_trace import observe_writes

@dataclass(frozen=True)
class ReplayAssumptions:
    clock_hz: float = 96_000_000
    sample_rate: int = 48_000
    gate_mask: int = 0x0008             # hypothetical; NOT physical RF TX_EN proof
    gate_active_high: bool = True
    gain_source: str = 'driver6'       # driver6, bias4, product, gate_only
    attack_ms: float = 0.0             # optional schematic RF response
    release_ms: float = 0.0

    def validate(self):
        if (not math.isfinite(self.clock_hz) or self.clock_hz <= 0 or
            type(self.sample_rate) is not int or not 1000 <= self.sample_rate <= 384000 or
            type(self.gate_mask) is not int or self.gate_mask <= 0 or
            self.gate_mask > 0xffff or (self.gate_mask & (self.gate_mask-1)) or
            self.gain_source not in ('driver6','bias4','product','gate_only') or
            any(not math.isfinite(v) or not 0 <= v <= 2000 for v in
                (self.attack_ms,self.release_ms))):
            raise ValueError('invalid or unqualified register replay assumptions')


def _next_sample(cycles, clock_hz, rate):
    """First sample whose time is >= the completed register write time."""
    return max(0, math.ceil((cycles * rate / clock_hz)-1e-10))


def replay_envelope(writes, samples, *, options=ReplayAssumptions()):
    """Return modeled envelope and provenance, with no inferred PA state.

    Unknown REG30 gate state is off. Unknown driver/bias initial settings are
    also zero (unless gain_source=gate_only). Events are applied to the first
    sample >= event timestamp; writes after recording end are ignored.
    """
    options.validate()
    if not isinstance(samples,int) or not 0 < samples <= 1_000_000:
        raise ValueError('samples must be a positive bounded integer')
    observed=observe_writes(writes,clock_hz=options.clock_hz)
    target=np.zeros(samples,dtype=np.float64)
    gated=np.zeros(samples,dtype=np.bool_)
    commands=observed['events']
    gate=False
    gain6=0
    bias4=0
    last=0
    accepted=0
    writes_after_end=0
    for entry in commands:
        # The parser already validated timestamps, register and value bounds.
        cycle=entry['cycles']
        sample=_next_sample(cycle,options.clock_hz,options.sample_rate)
        if sample >= samples:
            writes_after_end += 1
            continue
        if sample > last:
            if options.gain_source=='gate_only':gain=1.
            elif options.gain_source=='driver6':gain=gain6/63
            elif options.gain_source=='bias4':gain=bias4/15
            else:gain=(gain6/63)*(bias4/15)
            target[last:sample] = gain if gate else 0.
            gated[last:sample] = gate
        reg=int(entry['register'],16)
        val=int(entry['value'],16)
        if reg==0x30:
            gate=bool(val & options.gate_mask)==options.gate_active_high
        if reg==0x36:gain6=val & 0x3f
        if reg==0x19:bias4=val & 0xf
        last=sample
        accepted+=1
    if options.gain_source=='gate_only':gain=1.
    elif options.gain_source=='driver6':gain=gain6/63
    elif options.gain_source=='bias4':gain=bias4/15
    else:gain=(gain6/63)*(bias4/15)
    target[last:]=gain if gate else 0.
    gated[last:]=gate
    if options.attack_ms==0 and options.release_ms==0:
        out=target
    else:
        # The analog PA response is a hypothetical two-time-constant filter.
        out=np.empty_like(target)
        state=0.0
        for i,want in enumerate(target):
            t_ms=options.attack_ms if want>=state else options.release_ms
            alpha=1. if t_ms==0 else -math.expm1(-1000/(options.sample_rate*t_ms))
            state += alpha*(want-state)
            out[i]=state
    return out,dict(source='simulated_FD6818B_register_commands_only',
                    physical_rf=False,rf_transfer_calibrated=False,
                    notes='REG30 gate mask and 6/4-bit normalized gains are hypotheses, not hardware values',
                    assumed_gate_mask=f'0x{options.gate_mask:04X}',
                    gain_source=options.gain_source,
                    attack_ms=options.attack_ms,release_ms=options.release_ms,
                    total_events=observed['event_count'],
                    applied_events=accepted,after_end_events=writes_after_end,
                    gated_samples=int(np.count_nonzero(gated)),
                    generated_samples=samples,
                    mux_conflict_notices=observed['mux_conflict_notices'])


def carrier_iq(envelope,*,frequency_hz=0.,rate=48_000):
    """Pure modeled carrier; no complex TX hardware inferred."""
    a=np.asarray(envelope,dtype=float)
    if a.ndim!=1 or not np.isfinite(a).all() or (a<0).any():
        raise ValueError('finite nonnegative envelope required')
    if not math.isfinite(frequency_hz) or abs(frequency_hz)>=rate*.45:
        raise ValueError('frequency outside supported baseband interval')
    return (a*np.exp(2j*np.pi*frequency_hz*np.arange(len(a))/rate)).astype(np.complex64)


def schedule_cw_envelope(envelope,*,clock_hz=96_000_000,
                         rate=48_000,write_us=117.,gate_mask=0x8):
    """Generate a synthetic 3-wire command fixture, NOT from real firmware.

    Converts desired keying envelope to 6-bit driver commands at a requested
    update period. Includes an initial unkeyed gate state, then a gate-enable
    request before the first nonzero sample and a gate-disable on key-up.
    Test-only input to the same replay used for actual emulator bus captures.
    """
    a=np.asarray(envelope,dtype=float)
    if a.ndim!=1 or not 1<=len(a)<=1_000_000 or not np.isfinite(a).all() or (a<0).any() or (a>1).any():
        raise ValueError('bounded 0..1 envelope required')
    if not math.isfinite(write_us) or write_us<=0 or write_us>1e6:
        raise ValueError('positive simulated write delay required')
    ReplayAssumptions(clock_hz=clock_hz,sample_rate=rate,gate_mask=gate_mask).validate()
    # Each update consumes one complete software 3-wire transaction. There
    # are no simultaneous gain+gate writes, and stale intermediate gain
    # requests are *dropped*, not queued until after the recording ends.
    period=max(1,math.ceil(rate*write_us*1e-6))
    results=[(0,0x30,0)]                 # explicitly unknown PA gate -> off
    gain=0
    gate=False
    for n in range(period,len(a),period):
        requested=int(round(float(a[n])*63))
        code=None
        if requested==0 and gate:
            code=(0x30,0)
            gate=False
        elif requested>0 and not gate:
            if gain==0:
                # Apply first nonzero drive setting before enabling PA.
                code=(0x36,requested)
                gain=requested
            else:
                # Enable on the next available slot, even while the target
                # ramp continues; otherwise a changing ramp could prevent
                # key-down until its plateau (lost rise envelope).
                code=(0x30,gate_mask)
                gate=True
        elif requested>0 and gate and gain!=requested:
            code=(0x36,requested)
            gain=requested
        elif requested==0 and not gate and gain:
            code=(0x36,0)
            gain=0
        if code is not None:
            results.append((math.ceil(n*clock_hz/rate),*code))
    if gate:
        results.append((math.ceil(len(a)*clock_hz/rate),0x30,0))
    return results
