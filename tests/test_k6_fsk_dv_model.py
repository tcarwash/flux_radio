from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from k6_fsk_dv_model import PacketPlan, RESTART_S, simulate


def test_reproduces_measured_two_frame_gross_rate():
    observed_two_frame_cycle = 1024 / 4800 + RESTART_S
    assert 1024 / observed_two_frame_cycle == pytest.approx(3937.67, abs=.02)


def test_rate_half_protection_still_carries_sotto_1200_in_model():
    r = simulate(PacketPlan("1200", 8, 1, 2), packets=100,
                 loss=0, jitter_s=0, playout_delay_s=0)
    assert r["fifo_fit"]
    assert r["modeled_sustains_codec_rate"]
    assert r["protected_bytes_on_air"] == 108


def test_loss_and_jitter_are_deterministic_and_mark_missing_audio():
    p = PacketPlan("600", 4, 1, 2)
    a = simulate(p, packets=100, loss=.1, jitter_s=.15,
                 playout_delay_s=.6, seed=17)
    b = simulate(p, packets=100, loss=.1, jitter_s=.15,
                 playout_delay_s=.6, seed=17)
    assert a == b
    assert a["total_packet_deadline_misses"] >= a["observed_simulated_packet_losses"]
    assert a["modeled_missing_audio_ms"] > 0
    assert a["physical_rf_evidence"] is False


def test_rejects_packet_larger_than_fifo():
    with pytest.raises(ValueError, match="does not fit"):
        simulate(PacketPlan("1200", 16, 1, 2))
