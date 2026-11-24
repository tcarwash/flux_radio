"""Strictly passive FD6818B register-write timeline classification.

This only inspects simulated 3-wire register events captured by K6Board.
A write is *intent*, not proof that a physical RF path is enabled, that
multiple paths sum, or that an RF waveform of a named modulation exists.
"""
from __future__ import annotations
import math
from collections import Counter

CLOCK_HZ=96_000_000


def observe_writes(events,clock_hz=CLOCK_HZ):
    if not isinstance(events,(tuple,list)):
        raise ValueError('events must be a list of (cycle, register, value)')
    if not math.isfinite(clock_hz) or clock_hz<=0:
        raise ValueError('clock_hz must be positive finite')
    prior=-1.
    registers={}
    out=[]
    intents=Counter()
    for i,event in enumerate(events):
        if len(event)!=3:raise ValueError('write event needs exactly 3 elements')
        cycles,reg,val=event
        if (not isinstance(reg,int) or not isinstance(val,int)
                or not 0<=reg<128 or not 0<=val<65536
                or not math.isfinite(cycles) or cycles<prior or cycles<0):
            raise ValueError('timestamp/order/register/value invalid')
        prior=cycles
        was=registers.get(reg)
        names=[]
        if reg==0x30 and (was is None or (was^val)&8):
            names.append('tx_driver_gate_write')
        if reg==0x36:
            if was is None or ((was^val)&0xff00):names.append('apc_target_write')
            if was is None or ((was^val)&0x3f):names.append('driver6_write')
        if reg==0x19 and (was is None or (was^val)&0x0f):names.append('bias4_write')
        if reg==0x40 and (was is None or (was^val)&0x0fff):names.append('fsk_deviation_scale_write')
        if reg==0x51:
            if val&(1<<14):names.append('external_gp0_mux_request')
            else:names.append('subaudio_mux_or_gain_write')
        if reg==0x52:names.append('ctcss_phase_or_control_write')
        if reg in (0x58,0x59,0x5D,0x5E,0x5F):names.append('fsk_engine_register_write')
        if reg==0x70:
            names.append('tone_or_fsk_deviation_write_ambiguous')
        if reg==0x71:names.append('tone1_frequency_write')
        if reg==0x72:names.append('fsk_baud_or_dtmf_tone2_write_ambiguous')
        if reg==0x3F:names.append('modulation_mux_selector_write')
        if reg in (0x2B,0x47):names.append('fm_audio_filter_control_write')
        registers[reg]=val
        for k in names:intents[k]+=1
        out.append(dict(cycles=cycles,time_us=round(cycles*1e6/clock_hz,4),
                        register=f'0x{reg:02X}',value=f'0x{val:04X}',
                        previous=None if was is None else f'0x{was:04X}',
                        intent_hints=names))
    notices=[]
    names=set(intents)
    if 'fsk_engine_register_write' in names and 'tone_or_fsk_deviation_write_ambiguous' in names:
        notices.append('REG70 may be shared FSK-deviation control; do not infer simultaneous audio tone')
    if 'fsk_baud_or_dtmf_tone2_write_ambiguous' in names:
        notices.append('REG72 is baud in native FSK and second tone in DTMF; decode mode before interpreting')
    if 'subaudio_mux_or_gain_write' in names and 'external_gp0_mux_request' in names:
        notices.append('REG51 internal/external subaudio mux changed; not proof both sources coexist')
    if 'apc_target_write' in names and 'driver6_write' in names:
        notices.append('REG36 APC and driver-gain writes present; no calibrated combined RF envelope')
    return dict(source='K6Board simulated FD6818B 3-wire writes; no hardware',
                hardware_qualified=False,event_count=len(out),events=out,
                intent_counts=dict(intents),mux_conflict_notices=notices,
                final_register_values={f'0x{k:02X}':f'0x{v:04X}' for k,v in sorted(registers.items())})
