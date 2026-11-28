"""Opt-in cooperative FSK29 preload exercises the actual production Rust callback.

No actual FD6818, UART, RF, flash, PTT, serial cable, or GPIO is used.
The native mock injects 400us per SPI register access (not a chip measurement).
"""
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest

from tools.k6_sotto_tail_timing import test_vector as known_packet

ROOT=Path(__file__).resolve().parents[1]
HARNESS=ROOT/'tests/data/k6/sotto_api29_native_host.rs'
HOST=ROOT/'firmware/k6-aura/sotto-api29-async-fsk.rs.inc'


@pytest.mark.skipif(shutil.which('rustc') is None,reason='native Rust compiler missing')
def test_cooperative_preload_exact_sequence_and_fail_closed_in_host(tmp_path):
    assert 'include!("../../../firmware/k6-aura/sotto-api29-async-fsk.rs.inc");' in HARNESS.read_text()
    assert '#[cfg(k6_sotto_fsk_coop_preload)]' in HOST.read_text()
    exe=tmp_path/'coop-fsk29'
    build=subprocess.run(['rustc','--edition=2021','--test','--cfg','k6_sotto_async_fsk',
                          '--cfg','k6_sotto_fsk_coop_preload','-A','warnings',
                          str(HARNESS),'-o',str(exe)],capture_output=True,text=True,timeout=60)
    assert build.returncode==0,build.stderr
    run=subprocess.run([str(exe),'--nocapture','--test-threads=1'],
                       env={**os.environ,'SOTTO_API29_KNOWN_HEX':known_packet().hex()},
                       capture_output=True,text=True,timeout=30)
    assert run.returncode==0,run.stdout+'\n'+run.stderr
    assert '7 passed; 0 failed' in run.stdout
    m=re.search(r'COOP max_callback_ticks=(\d+) fifo_words=(\d+) rf_start_after_full_fifo=(\d+)',run.stdout)
    assert m is not None,run.stdout
    max_ticks,words,correct=map(int,m.groups())
    assert 0<max_ticks<=50 and words==33 and correct==1


def test_coop_build_flag_is_off_by_default_and_requires_correct_host():
    build=(ROOT/'firmware/k6-aura/build-fd6818b.sh').read_text()
    assert 'case "${K6_SOTTO_FSK_COOP_PRELOAD:-0}" in' in build
    assert '--cfg k6_sotto_fsk_coop_preload' in build
    assert '[ "$K6_SOTTO_ASYNC_FSK" = 1 ]' in build
    assert '[ "$K6_SOTTO_MIC" = 1 ]' in build
    assert '[ "${K6_RAM0:-0}" = 1 ]' in build
    assert 'k6_sotto_fsk_coop_preload' in (ROOT/'firmware/k6-aura/sotto-gp0-fault.rs.inc').read_text()
    # Prevent the optional staged scratch pointer from eating the
    # fielded stack reservation: both statics belong in protected RAM0.
    host=(ROOT/'firmware/k6-aura/sotto-api29-async-fsk.rs.inc').read_text()
    assert host.count('#[cfg_attr(k6_sotto_ram0_state, link_section=".ram0")]')>=4
