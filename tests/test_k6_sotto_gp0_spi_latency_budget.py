"""Offline SOTTO GP0 regression: include SPI byte-transfer time.

The default emulator's fixed cost per resource_read can conceal a real-time
PCM producer/consumer overrun. These timings are *scenarios*, not measured
radio latencies. No physical radio access or transmission.
"""
import numpy as np
import pytest
from sim.k6emu.rf.virtual_link import Channel
from tools.k6_sotto_virtual_link import prepare, trial, accepted


@pytest.fixture(scope="module")
def gp0_timing_module(tmp_path_factory):
    # 0.41 seconds, enough to exercise the first RF packet and stress the
    # four-block 8kHz PCM queue without a lengthy end-to-end trial.
    sample = np.arange(3280)
    pcm = (9000*np.sin(2*np.pi*320*sample/8000)).astype("<i2")
    from pathlib import Path
    return prepare(Path(__file__).resolve().parents[1],
                   tmp_path_factory.mktemp("gp0-spi-latency"),pcm=pcm,gp0=True)


@pytest.mark.parametrize("spi_byte_us", [0.0, 0.5, 1.0, 1.5])
def test_bounded_flash_byte_overhead_in_model(gp0_timing_module, spi_byte_us):
    result = trial(gp0_timing_module, Channel(), cpi=1.5,
                   spi_us=34, spi_byte_us=spi_byte_us, adc_cycles=2400)
    assert accepted(result), result
    assert result["session"]["complete"]
    assert not result["pcm_lost"]
    assert result["samples"] == 3280


def test_slow_flash_bytes_expose_four_block_pcm_overflow(gp0_timing_module):
    # This failure must never be mistaken for completed digital voice. The
    # sensitivity threshold depends on synthetic CPU/ISR assumptions and
    # must not be promoted to a real-radio measurement.
    result = trial(gp0_timing_module, Channel(), cpi=1.5,
                   spi_us=34, spi_byte_us=2.0, adc_cycles=2400)
    assert not accepted(result)
    assert result["module_return"] < 0
    assert result["pcm_lost"]
    assert result["pcm_highwater"] == 4
    assert result["samples"] < result["samples_expected"]
    assert not result["session"]["complete"]
    assert not result["lease_expired"]
