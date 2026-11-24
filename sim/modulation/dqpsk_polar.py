"""Experimental constant-envelope DQPSK via a *hypothetical* signed-FM DAC.

No arbitrary-IQ transmitter, PA/GPIO control, real SPI, modem install, or RF.
A phase increment per symbol is implemented as the integral of a requested
signed frequency, quantized and delivered no earlier than a simulated 3-wire
write completion. This is NOT REG52 CTCSS phase control (which is low-frequency
subaudio tone phase, not RF carrier phase) and is NOT a validated REG51 DAC.
"""
from __future__ import annotations
from dataclasses import dataclass
import math
import numpy as np

RATE=48000
PHASES=np.array([0.,math.pi/2.,-math.pi/2.,math.pi],dtype=float)

@dataclass(frozen=True)
class Parameters:
    baud: int=100
    spi_write_us: float=117.
    analog_bandwidth_hz: float=135.
    max_deviation_hz: float=1500.
    dac_bits: int=7
    seed: int=42

    def validate(self):
        if (not isinstance(self.baud,int) or not 10<=self.baud<=1200 or RATE%self.baud or
            not math.isfinite(self.spi_write_us) or not 0<self.spi_write_us<=1e6 or
            not math.isfinite(self.analog_bandwidth_hz) or not 0<=self.analog_bandwidth_hz<=5000 or
            not math.isfinite(self.max_deviation_hz) or not 10<=self.max_deviation_hz<=5000 or
            self.dac_bits not in (4,7) or
            not isinstance(self.seed,int) or not 0<=self.seed<2**32):
            raise ValueError('invalid hypothetical signed-deviation PHY assumptions')


def public_frame(params:Parameters,payload_symbols:int=128):
    params.validate()
    if not isinstance(payload_symbols,int) or not 8<=payload_symbols<=1024:
        raise ValueError('payload length outside simulated 8..1024 symbol bounds')
    # Four public preamble phases train instantaneous phase response. A fixed
    # 16-symbol sync validates the decision mapping without inspecting data.
    pilot=np.tile(np.array([0,1,3,2],dtype=np.int8),16)
    sync=np.tile(np.array([3,0,2,1],dtype=np.int8),4)
    payload=np.random.default_rng(params.seed).integers(0,4,payload_symbols,dtype=np.int8)
    return np.r_[pilot,sync,payload],len(pilot),len(sync)


def synthesize(params:Parameters,*,payload_symbols=128,reverse_sign=False,
               disable_updates=False):
    """Return FM IQ and one-completion-per-changing-symbol REG51 intentions.

    Fixed frequency per symbol is intended to integrate to [0,90,-90,180]
    degree phase increments; it is NOT ideal RRC QPSK. The reference is
    differential QPSK, with absolute phase retaining continuous trajectory.
    """
    syms,pilot_n,sync_n=public_frame(params,payload_symbols)
    sps=RATE//params.baud
    desired=PHASES[syms]*params.baud/(2*math.pi)
    if np.max(np.abs(desired))>params.max_deviation_hz:
        raise ValueError('requested per-symbol phase step exceeds model deviation ceiling')
    step=params.max_deviation_hz/(2**params.dac_bits-1)
    desired=np.round(desired/step)*step
    if reverse_sign:desired=-desired
    write_samples=max(1,math.ceil(params.spi_write_us*RATE*1e-6))
    samples=(len(syms)+3)*sps
    # Advance commands causally on the same sample clock, one register write
    # per update and no later than the next symbol's completion if fast enough.
    freq=np.zeros(samples,dtype=float)
    events=[];current=0.;prior=0;deadline=0;dropped=0
    for k,level in enumerate(desired):
        start=k*sps
        if disable_updates:continue
        if abs(current-level)<1e-10:continue
        if start<deadline:
            dropped+=1
            continue
        completed=start+write_samples
        if completed>=samples:break
        freq[prior:completed]=current
        prior=completed
        deadline=completed
        current=float(level)
        sign=0x2000 if current<0 else 0
        magnitude=min(127,int(round(abs(current)/params.max_deviation_hz*127)))
        # Purely schematic encoding; not a hardware-safe REG51 write.
        events.append((completed,0x51,0x8000|sign|magnitude))
    freq[prior:]=current
    # Schedule explicit neutral at the end; not used to decode payload. Delay
    # and analog tail are reported, not claimed to be gated physical RF.
    final_time=len(syms)*sps+write_samples
    if final_time<samples:
        events.append((final_time,0x51,0x8000))
        freq[final_time:]=0.
    if params.analog_bandwidth_hz:
        alpha=-math.expm1(-2*math.pi*params.analog_bandwidth_hz/RATE)
        analog=np.empty_like(freq)
        state=0.
        for i,v in enumerate(freq):
            state+=alpha*(v-state)
            analog[i]=state
        freq=analog
    # The radio's phase is the integral of instantaneous frequency. There
    # are no discrete arbitrary carrier phase jumps in this simulation.
    phase=np.cumsum(2*math.pi*freq/RATE)
    iq=np.exp(1j*phase).astype(np.complex64)
    return dict(iq=iq,instantaneous_frequency_hz=freq,
                symbols=syms,pilot_n=pilot_n,sync_n=sync_n,
                writes=events,dropped_writes=dropped,
                sample_rate=RATE,samples_per_symbol=sps,baud=params.baud,
                max_desired_hz=float(np.max(np.abs(desired))),
                continuous_phase=True,constant_envelope=True,
                physical_tx=False,hardware_qualified=False,
                path_evidence='REG51_signed_multilevel_hypothesis_not_qualified')
