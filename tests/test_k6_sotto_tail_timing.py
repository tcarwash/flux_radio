"""Offline-only source-anchored hypothesis/regression tests (not RF claims)."""
import hashlib
from pathlib import Path
import numpy as np
import pytest
from tools import k6_sotto_tail_timing as t
from modem import pager


def test_packet_fixture_is_canonical_valid_33_word_l64():
    physical=t.test_vector()
    assert len(physical)==66 and physical[-2:]==bytes.fromhex("5aa5")
    p=pager.profile("L","1200",depth=1).unpack(physical[:64])
    assert p is not None
    assert p["msg_id"]==112 and p["seq"]==0 and p["total"]==8
    assert b"".join(p["frames"])==bytes(range(48))
    assert len(hashlib.sha256(physical).digest())==32


def test_source_contract_has_explicit_fifo_load_then_pa_then_tx():
    c=t.source_contract()
    assert c["expected_fifo_words"]==33
    assert c["declared_length_register"]=="0x4100"
    assert len(c["host_sha256"])==64


def test_full_air_frame_delivers_guard_after_crc():
    c=t.run_case("nominal")
    p=c["predicted"]
    assert c["frame"]["data_crc_valid"]
    assert p["transmitted_bits"]==528 and p["strict_delivery"]
    assert p["first_missing_byte"] is None
    assert p["air_done_ms"]==110


def test_early_irq_poll_timing_can_explain_cutoff_near_byte_54():
    for period,phase in ((5,0),(7,3)):
        c=t.run_case("early_done",irq_at_ms=90,poll_ms=period,phase_ms=phase)
        p=c["predicted"]
        assert 53<=p["first_missing_byte"]<=59
        assert not p["crc_bytes_fully_transmitted"]
        assert not p["strict_delivery"]


def test_early_irq_jitter_changes_missing_byte():
    c1=t.run_case("early_done",irq_at_ms=90,poll_ms=5,phase_ms=0)
    c2=t.run_case("early_done",irq_at_ms=90,poll_ms=7,phase_ms=3)
    assert c1["predicted"]["transmitted_bits"]!=c2["predicted"]["transmitted_bits"]


def test_early_irq_delayed_cleanup_vs_independent_pa_drop():
    # Mathematical discriminating experiment, NOT a safe firmware patch.
    held=t.run_case("early_done",irq_at_ms=90,post_irq_hold_ms=20)
    assert held["predicted"]["strict_delivery"]
    lost=t.run_case("early_pa_drop",irq_at_ms=90,pa_drop_at_ms=90,post_irq_hold_ms=20)
    assert not lost["predicted"]["crc_bytes_fully_transmitted"]


def test_pa_ready_drop_truncates_even_when_irq_is_on_time():
    c=t.run_case("early_pa_drop",pa_drop_at_ms=90)
    assert c["predicted"]["first_missing_byte"]==54
    assert not c["predicted"]["crc_bytes_fully_transmitted"]


def test_32_word_hypothesis_is_physical_guard_failure_but_contradicted_by_fsk14():
    c=t.run_case("fifo_limit",fifo_capacity=32)
    assert c["events"]["preloaded_fifo_words"]==32
    assert c["predicted"]["transmitted_whole_bytes"]==64
    assert c["predicted"]["crc_bytes_fully_transmitted"]
    assert not c["predicted"]["guard_bytes_fully_transmitted"]


def test_rendered_synthetic_iq_has_modulation_until_cutoff_then_flat():
    full=t.render_iq(t.run_case("nominal"))
    bad=t.render_iq(t.run_case("early_done",irq_at_ms=90))
    assert full.dtype==bad.dtype==np.complex64
    assert full.shape==bad.shape
    fs=128000
    # On the same absolute symbol clock, the already-truncated frame goes flat.
    start=int((.025+(160+54*8)/4800)*fs)
    end=int((.025+(160+59*8)/4800)*fs)
    a=np.angle(full[1:]*np.conj(full[:-1]))[start:end]
    b=np.angle(bad[1:]*np.conj(bad[:-1]))[start:end]
    assert np.percentile(a,90)-np.percentile(a,10)>.08
    assert np.percentile(b,90)-np.percentile(b,10)<.01


@pytest.mark.parametrize("value",[-1,50.001,51])
def test_hold_is_finite(value):
    with pytest.raises(ValueError):
        t.run_case("early_done",post_irq_hold_ms=value)
