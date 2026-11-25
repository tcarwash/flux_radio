"""Opt-in two-ARM virtual RF -> compiled Cortex-M0 Sotto decoder -> PCM/PWM tests.

Requires a byte-matched experimental API28-31 K6_RAM0=1 ELF/image pair;
never accesses a serial device, physical radio, microphone, SDR, or speaker DMA.
"""
import os
from pathlib import Path
import pytest

from tools.k6_sotto_arm_rxplay_gate import run

ROOT = Path(__file__).resolve().parents[1]


def _require_env():
    if os.environ.get("K6_SIM_SOTTO_LIVE_ARM") != "1":
        pytest.skip("Set K6_SIM_SOTTO_LIVE_ARM=1 with matching ELF/image")
    elf = os.environ.get("K6_SIM_SOTTO_LIVE_ELF")
    image = os.environ.get("K6_SIM_SOTTO_LIVE_IMAGE")
    if not elf or not image:
        pytest.fail("Missing explicit K6_SIM_SOTTO_LIVE_ELF/IMAGE")
    return Path(elf).resolve(), Path(image).resolve()


@pytest.mark.parametrize("blocks", [8, 32, 80])
def test_arm_sotto_rx_decode_pcm_pwm_exact(blocks):
    elf, image = _require_env()
    r = run(
        elf, image, ROOT,
        blocks=blocks,
        max_quanta=24000 if blocks == 8 else 90000,
        include_corruption=(blocks == 8),
    )
    assert r["result"] == "PASS"
    assert r["real_arm_rx_decoder"] is True
    assert r["host_decoder_used_for_arm_output"] is False
    assert r["dma_speaker_validated"] is False
    assert r["pcm_exact_match"] is True
    assert r["pwm_exact_match"] is True
    assert r["pcm_samples"] == 320 * r["codec_frames"]
    assert r["pool_blocks_end"] == 0
    assert 0 < r["pool_peak_bytes"] <= 3200
    assert r["cb_reads"] > 0
    if blocks == 8:
        c = r["corruption_tests"]
        assert c["bad_crc_return"] == -11
        assert c["missing_packet_return"] == -10
        assert c["bad_codebook_return"] == -13
        assert c["all_cleaned_up"] is True
