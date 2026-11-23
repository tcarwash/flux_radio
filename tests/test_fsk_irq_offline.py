"""Offline-only regression tests; no RF, serial or hardware access."""
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from fsk_irq_offline import (
    fd6818_event_irq_predicate, fsk09_original_irq_predicate, simulate,
)


@pytest.mark.parametrize("reg02,old,new", [
    (0x000E, False, True),     # event code only
    (0x080E, False, True),     # unrelated high flag plus event code
    (0x0800, False, False),    # actual FSK-09 IRQ telemetry
    (0x4000, True, False),     # older incorrect bit-test can false-trigger
    (0x400E, True, True),
    (0x000F, False, False),    # end-of-frame event must not grant refill
    (0x080F, False, False),    # unrelated flags plus frame-complete event
    (0x0000, False, False),
])
def test_irq_qualification(reg02, old, new):
    assert fsk09_original_irq_predicate(reg02) is old
    assert fd6818_event_irq_predicate(reg02) is new


def test_source_audit_keeps_original_as_control():
    original = (ROOT / "c/k6/mod/fsk_refill.c").read_text()
    assert "#define TX_FIFO_AE 0x4000u" in original
    assert "irq&TX_FIFO_AE" in original
    assert "R_IRQ 0x02u" in original
    assert "R_WORK 0x3fu" in original
    assert "R_FIFO_LEVEL 0x5eu" in original


def test_event_decoded_single_frame_refill_under_assumptions():
    result = simulate()
    assert result.outcome == "COMPLETE"
    assert result.words_loaded == result.words_consumed == 128
    assert result.appended_batches == [32, 32, 16]
    assert result.words_match
    assert result.maximum_occupancy <= 64
    assert result.irq_observations == ["0x000e"]
    assert result.append_times_ms[0] >= 80
    assert result.time_ms in (426, 427)


def test_original_bit_predicate_misses_encoded_event_and_underflows():
    result = simulate(decoder="original", irq_source="event")
    assert result.outcome == "UNDERFLOW"
    assert result.words_loaded == 48
    assert result.appended_batches == []
    assert result.irq_observations == ["0x000e"]


@pytest.mark.parametrize("decoder", ["event", "original"])
def test_real_trace_does_not_indicate_fifo_refill(decoder):
    result = simulate(decoder=decoder, irq_source="observed_0800")
    assert result.outcome == "UNDERFLOW"
    assert result.irq_observations == ["0x0800"]
    assert not result.appended_batches


@pytest.mark.parametrize("decoder", ["event", "original"])
def test_missing_interrupt_aborts_without_writing(decoder):
    result = simulate(decoder=decoder, irq_source="none")
    assert result.outcome == "UNDERFLOW"
    assert result.appended_batches == []


def test_hypothetical_irq_bit_model_matches_old_predicate():
    result = simulate(decoder="original", irq_source="bit")
    assert result.outcome == "COMPLETE"
    assert result.words_match
    assert result.irq_observations == ["0x4000"]


def test_delayed_refill_tolerable_with_headroom():
    result = simulate(service_delay_ms=30)
    assert result.outcome == "COMPLETE"
    assert result.words_match


def test_delayed_refill_exceeds_headroom_and_underflows():
    result = simulate(service_delay_ms=90)
    assert result.outcome == "UNDERFLOW"


def test_refill_at_empty_boundary_is_rejected():
    # 24 words of threshold headroom = 80 ms; same-tick refill is unsafe.
    result = simulate(service_delay_ms=80)
    assert result.outcome == "UNDERFLOW"
    assert result.time_ms == 160
    assert not result.appended_batches


def test_refill_just_before_empty_is_only_model_success():
    # The 1-ms/zero-SPI-cost model admits this. Real firmware needs margin.
    result = simulate(service_delay_ms=79)
    assert result.outcome == "COMPLETE"


def test_declared_length_prohibits_excess_refill():
    result = simulate(frame_words=64)
    assert result.outcome == "FRAME_LENGTH_EXCEEDED"


def test_too_small_capacity_for_first_refill():
    result = simulate(capacity_words=48, threshold_words=24)
    assert result.outcome == "FIFO_OVERFLOW"


def test_word_clock_at_preload_and_threshold():
    result = simulate(decoder="event", irq_source="event")
    assert result.append_times_ms[0] == 80
    assert simulate(irq_source="observed_0800").time_ms == 160
