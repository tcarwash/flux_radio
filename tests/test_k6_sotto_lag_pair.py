"""Cortex-M0 paired pitch correlation: exact offline codec/RF and abort checks.

Uses compiled modules and modelled GP0 capture; never touches real RF hardware.
"""
from pathlib import Path
import hashlib
import numpy as np
import pytest

from tools.k6_sotto_module import (
    build, SOTTO_ARENA_BYTES, SOTTO_SCRATCH_RESERVE,
    SOTTO_MODULE_OVERHEAD, SOTTO_LIVE_SCRATCH_REQUIRED,
)
from tools.k6_sotto_virtual_link import prepare, trial, accepted
from sim.k6emu.rf.virtual_link import Channel

ROOT = Path(__file__).resolve().parents[1]
MODEL = dict(spi_us=6.19212962962963, spi_byte_us=0.9403935185185185, fd_us=60)
BASE_FLAGS = dict(fast_div=True, fast_history_copy=True, fast_cb_pair=True)


def _pcm(n, mixed):
    t = np.arange(n, dtype=float) / 8000
    if not mixed:
        return (7000 * np.sin(2 * np.pi * 320 * t) +
                2000 * np.sin(2 * np.pi * 870 * t)).astype('<i2')
    x = np.zeros(n, dtype=float)
    a = n // 6
    x[a:2*a] = .5 * np.sin(2*np.pi*185*t[a:2*a])
    x[2*a:3*a] = (.35*np.sin(2*np.pi*315*t[2*a:3*a]) +
                  .2*np.sin(2*np.pi*631*t[2*a:3*a]))
    x[3*a:4*a] = (.2*np.sin(2*np.pi*250*t[3*a:4*a]) +
                  .16*np.sin(2*np.pi*730*t[3*a:4*a]))
    x[4*a:5*a] = np.random.default_rng(2711).normal(0, .24, size=a)
    x[5*a:] = (.22*np.sin(2*np.pi*110*t[5*a:]) +
               .3*np.sin(2*np.pi*540*t[5*a:]))
    return (x*20000).clip(-32768, 32767).astype('<i2')


@pytest.fixture(scope='module')
def codes():
    old, oe = build(host_resources=True, gp0_irq_tx=True, **BASE_FLAGS)
    new, ne = build(host_resources=True, gp0_irq_tx=True,
                    **BASE_FLAGS, fast_lag_pair=True)
    return old, oe, new, ne


def _margin(code):
    return (SOTTO_ARENA_BYTES - SOTTO_SCRATCH_RESERVE -
            SOTTO_MODULE_OVERHEAD - len(code) - SOTTO_LIVE_SCRATCH_REQUIRED)


def test_paired_lag_guard_and_size(codes):
    old, oe, new, ne = codes
    assert oe == ne == 16
    assert len(old) == 9756 and _margin(old) == 76
    assert len(new) == 9784 and _margin(new) == 48
    assert hashlib.sha256(new).hexdigest() == '9834e44bb81595a69c354c39fdc14b2a945e2fdc653a46293dd618b14e4e5bc0'
    # The stand-alone experiment exceeds our original 48-byte margin;
    # never package it as a physical candidate.
    with pytest.raises(ValueError, match='at least 48 are required'):
        build(host_resources=True, gp0_irq_tx=True, fast_lag_pair=True)
    source = (ROOT / 'c/fixed/sotto_fx_slim.c').read_text()
    assert 'fast_lag_pair' not in source
    assert 'for (i = 0; i < W4; i += SUB2)' in source


def _run(tmp_path_factory, codes, n, mixed, cpi, adc):
    old, oe, new, ne = codes
    scratch = tmp_path_factory.mktemp(f'lag-pair-{n}-{mixed}-{cpi}-{adc}')
    artifact = prepare(ROOT, scratch, pcm=_pcm(n, mixed), gp0=True)
    return (
        trial({**artifact, 'code': old, 'entry': oe}, Channel(), cpi=cpi, adc_cycles=adc, **MODEL),
        trial({**artifact, 'code': new, 'entry': ne}, Channel(), cpi=cpi, adc_cycles=adc, **MODEL)
    )


def test_three_second_mixed_capture_is_exact_and_queue_reduces(tmp_path_factory, codes):
    baseline, candidate = _run(tmp_path_factory, codes, 24000, True, 2.5, 4000)
    assert accepted(baseline) and accepted(candidate), (baseline, candidate)
    assert candidate['samples'] == candidate['samples_expected'] == 24000
    assert candidate['codec_exact'] and candidate['codec_frames'] == 75
    assert candidate['session']['complete'] and candidate['session']['valid'] == 12
    assert candidate['session']['invalid'] == 0 and candidate['wire_decoder_agrees']
    assert candidate['pcm_highwater'] <= baseline['pcm_highwater'] == 3
    assert candidate['pcm_highwater'] == 2 and not candidate['pcm_lost']
    assert candidate['gp0_starts'] == candidate['gp0_stops'] == 1
    assert not candidate['tx']['active'] and not candidate['lease_expired']


def test_harsh_mixed_fails_closed_but_progresses_further(tmp_path_factory, codes):
    old, new = _run(tmp_path_factory, codes, 9600, True, 3.0, 4000)
    assert old['pcm_lost'] and new['pcm_lost']
    assert old['samples'] == 3840 and new['samples'] == 4800
    assert new['module_return'] == -12 and new['pcm_highwater'] == 4
    assert not new['tx']['active'] and not new['session']['complete']
    assert new['gp0_starts'] == new['gp0_stops'] == 1


def test_harsh_tone_improves_without_ignoring_overrun(tmp_path_factory, codes):
    old, new = _run(tmp_path_factory, codes, 8293, False, 3.0, 4000)
    assert old['pcm_lost'] and new['pcm_lost']
    assert old['samples'] == 2880 and new['samples'] == 5440
    assert new['module_return'] == -12 and new['pcm_highwater'] == 4
    assert not new['tx']['active'] and not new['session']['complete']
    assert new['gp0_starts'] == new['gp0_stops'] == 1
