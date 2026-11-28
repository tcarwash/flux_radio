"""Build-only qualification for the current API31 Sotto PCM RF module.

This suite is software-only. It never opens a serial port, transmits,
modifies a radio, claims the bench, or starts an SDR.
"""
from pathlib import Path

import pytest

from modem import k6mod
from tools.k6_sotto_pcm_rf_build import build

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'c/k6/mod/sotto_fsk_drain_tx.c'


def test_explicit_api31_module_build_is_stable_and_armed_only_on_run():
    image,entry=build(settle_ms=20,interframe_ms=0)
    repeat,entry2=build(settle_ms=20,interframe_ms=0)
    assert image==repeat and entry==entry2==16
    assert k6mod.module_name(image)=="SOTTO PCM RF"
    assert 8500<len(image)<11500
    source=SOURCE.read_text()
    assert '#define SOTTO_DRAIN_ARMED 0' in source
    assert 'a->rx_hz()!=433500000u' in source
    assert 'a->tx_on(2800u)' in source
    assert 'a->tx_off()' in source
    assert 'SOTTO_DRAIN_KEEP_PA' in source
    assert 'SOTTO_DRAIN_SETTLE_MS' in source
    assert 'slim_encode10(encoder,pcm,six)' in source
    assert 'sl_finish(s)' in source
    assert 'a->cancel_poll' in source


def test_default_inert_module_has_no_rf_code_and_no_valid_armed_ffi():
    image,entry=k6mod.build(str(SOURCE))
    assert entry==16 and k6mod.module_name(image)=="SOTTO DRAIN"
    assert len(image)<350
    assert 'SOTTO DRAIN inert' in SOURCE.read_text()


def test_settle_and_interframe_settings_are_build_time_variants():
    standard,_=build(settle_ms=20,interframe_ms=0)
    no_settle,_=build(settle_ms=0,interframe_ms=0)
    gap,_=build(settle_ms=20,interframe_ms=60)
    assert standard!=no_settle and standard!=gap
    assert all(8500<len(x)<11500 for x in (standard,no_settle,gap))


@pytest.mark.parametrize("settle,gap",[(-1,0),(31,0),(20,-1),(20,101)])
def test_rejects_unbounded_lab_timing(settle,gap):
    with pytest.raises(ValueError,match="safe laboratory"):
        build(settle_ms=settle,interframe_ms=gap)
