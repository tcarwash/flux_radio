"""Deterministic *offline* FM symbol/3-wire update timing model.

The reference native-FSK FIFO has a separate hardware-controlled 4800-baud
symbol clock; it does not require one SPI write per FSK symbol. Binary
REG51 subaudio and signed multilevel deviation are distinct hypothetical
CPU-register-driven paths. No registers are written to physical hardware.
"""
from __future__ import annotations
from dataclasses import dataclass
import math
import numpy as np

RATE=48_000
PATHS=('native_fsk','subaudio','signed_dac','reg40_per_frame')

@dataclass(frozen=True)
class Settings:
    path: str = 'native_fsk'
    baud: int = 4800
    write_us: float = 117.
    bandwidth_hz: float = 0.
    deviation_hz: float = 1500.
    seed: int = 71

    def validate(self):
        if (self.path not in PATHS or type(self.baud) is not int or
            not 10<=self.baud<=4800 or RATE%self.baud!=0 or
            not math.isfinite(self.write_us) or not 0<self.write_us<=1e6 or
            not math.isfinite(self.bandwidth_hz) or not 0<=self.bandwidth_hz<=15000 or
            not math.isfinite(self.deviation_hz) or
            not 20<=self.deviation_hz<=5000 or
            type(self.seed) is not int or not 0<=self.seed<2**32 or
            (self.path in ('native_fsk','reg40_per_frame') and self.baud!=4800)):
            raise ValueError('unsupported or invalid offline FM model settings')


def frame(settings, payload_symbols=128):
    settings.validate()
    if not 8<=payload_symbols<=1024:
        raise ValueError('payload symbols must be 8..1024')
    order=4 if settings.path=='signed_dac' else 2
    pilot=np.tile(np.arange(order,dtype=np.int8),16)  # 32 or 64 publicly known
    sync=np.array(([0,order-1,1,order-2]*4) if order==4 else
                  ([0,0,1,1,1,0,1,0]*2),dtype=np.int8)
    rng=np.random.default_rng(settings.seed)
    payload=rng.integers(0,order,payload_symbols,dtype=np.int8)
    return np.concatenate((pilot,sync,payload)),len(pilot),len(sync)


def build(settings, *, payload_symbols=128, wrong_signed_order=False):
    """Waveform, source symbols, register-completion events and timing evidence.

    `wrong_signed_order` intentionally reverses the physical DAC levels while
    retaining the expected symbol labels: an independent negative control.
    """
    levels,pilot_n,sync_n=frame(settings,payload_symbols)
    sps=RATE//settings.baud
    amplitude=(levels.astype(float)*2-(3 if settings.path=='signed_dac' else 1))
    amplitude/=3 if settings.path=='signed_dac' else 1
    if wrong_signed_order and settings.path=='signed_dac':amplitude=-amplitude
    target=amplitude*settings.deviation_hz
    original=np.repeat(target,sps)
    # Tail enables a receiver to sample the final frame symbols even after its
    # FIR group delay; excludes tail from BER scoring.
    frequency=np.r_[original,np.full(sps*2,target[-1])]
    writes=[]
    missed=0
    delay_samples=0
    if settings.path in ('subaudio','signed_dac'):
        # Exactly one register-completion request at a time; new stale symbol
        # requests may not be queued for later transmission.
        duration=max(1,math.ceil(RATE*settings.write_us/1e6))
        deadline=0
        last_code=None
        applied=np.zeros(len(frequency))
        current=0.
        prev=0
        for k,value in enumerate(target):
            sample=k*sps
            if value!=last_code:
                if sample<deadline:
                    missed+=1
                    continue
                end=sample+duration
                # Speculative REG51 signed magnitude encoding from TX-27;
                # DCS-mode combined sign+gain is NOT RF-qualified. Binary
                # subaudio uses an equally schematic gain code here.
                magnitude=min(127,int(round(abs(value)/settings.deviation_hz*127)))
                reg51=0x8000 | (0x2000 if value<0 else 0) | magnitude
                writes.append((end,0x51,reg51))
                applied[prev:min(end,len(applied))]=current
                prev=min(end,len(applied))
                current=value
                last_code=value
                deadline=end
                delay_samples+=duration
        applied[prev:]=current
        frequency=applied
        # Treat analog LPF as uncertain PHY bandwidth—not proof of the chip's
        # REG07/REG51 actual symbol response or TX gating.
        if settings.bandwidth_hz:
            alpha=1-math.exp(-2*math.pi*settings.bandwidth_hz/RATE)
            y=np.empty_like(frequency)
            state=0.
            for k,z in enumerate(frequency):
                state+=alpha*(z-state)
                y[k]=state
            frequency=y
    elif settings.path=='reg40_per_frame':
        # REG40 changes only once before the frame, never within the FIFO
        # symbol stream. The actual native-FSK effect is not established.
        writes.append((0,0x40,0x0040))
        frequency*=.8
    else:
        # FIFO payload is already serialized by the hardware symbol clock.
        # No fabricated 3-wire write per data bit.
        writes.append((0,0x59,0x08f8))

    # Physical continuous-phase FM convention (not arbitrary IQ phase DAC).
    phase=np.cumsum(2*math.pi*frequency/RATE)
    iq=np.exp(1j*phase).astype(np.complex64)
    assert np.isfinite(iq).all()
    return dict(settings=settings,iq=iq,frequency_hz=frequency,
                source_levels=levels,pilot_n=pilot_n,sync_n=sync_n,
                writes=writes,missed_register_updates=missed,
                total_write_delay_samples=delay_samples,
                symbol_rate=settings.baud,order=(4 if settings.path=='signed_dac' else 2),
                phase_continuous=True,physical_rf=False,
                hardware_qualified=False)
