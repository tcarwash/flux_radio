"""Compile the unchanged production Rust API31 callback in a native FIFO mock."""
from __future__ import annotations
from pathlib import Path
import shutil
import os
import subprocess
import pytest
from test_k6_sotto_e2e_mock import built  # compile one pinned ARM/native fixture
ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'tests/data/k6/sotto_rx4800_native_host.rs'
RX=ROOT/'firmware/k6-aura/sotto-fsk4800-rx.rs.inc'

@pytest.mark.skipif(shutil.which('rustc') is None,reason='native rustc missing')
def test_true_firmware_rust_callback_fifo_ptt_timing_and_reg_restore(tmp_path):
    # include! directly compiles the production .rs.inc, not a ported stub.
    assert 'include!("../../../firmware/k6-aura/sotto-fsk4800-rx.rs.inc")' in SRC.read_text()
    assert 'unsafe extern "C" fn mod_fsk4800_rx(' in RX.read_text()
    exe=tmp_path/'rx4800-host-test'
    compiler=subprocess.run(['rustc','--edition=2021','--test',str(SRC),
        '--cfg','k6_fsk4800_rx','-A','warnings','-o',str(exe)],
        capture_output=True,text=True,timeout=75)
    assert compiler.returncode==0,compiler.stderr
    out=subprocess.run([str(exe),'--test-threads=1'],
                       capture_output=True,text=True,timeout=60)
    assert out.returncode==0,out.stdout+'\n'+out.stderr
    assert '6 passed' in out.stdout


@pytest.mark.skipif(shutil.which('rustc') is None,reason='native rustc missing')
def test_true_rust_host_decodes_iq_derived_fsk_words(tmp_path, built):
    """Actual ARM Sotto TX -> SoapyFang IQ -> actual Rust callback FIFO pump.

    PTT/FDV1/CRC in this pipeline remain separately validated by the C ARM
    receive consumer. The exact firmware Rust callback is tested as compiled
    native Rust code with a deterministic register model, not in MCU firmware.
    """
    from sim.k6emu.rf.virtual_link import Channel
    from tools.k6_sotto_virtual_link import trial, accepted
    art, _=built
    sample=trial(art,Channel(prefix_every=2),cpi=1.5,adc_cycles=4800,
                 spi_us=10,capture_rf_frames=True)
    assert accepted(sample)
    frames=sample['received_physical_frames']
    assert len(frames)==6 and all(len(f)==66 for f in frames)
    raw=tmp_path/'rf-fsk-frames.bin'
    raw.write_bytes(b''.join(frames))
    exe=tmp_path/'rx-host-rust'
    compiled=subprocess.run(['rustc','--edition=2021','--test',str(SRC),
        '--cfg','k6_fsk4800_rx','-A','warnings','-o',str(exe)],
        capture_output=True,text=True,timeout=75)
    assert compiled.returncode==0,compiled.stderr
    result=subprocess.run([str(exe),'--test-threads=1',
        '--exact','native_callback_streams_real_virtual_iq_receiver_frames'],
        capture_output=True,text=True,timeout=60,
        env={**os.environ,'SOTTO_RX4800_FRAMES':str(raw)})
    assert result.returncode==0,result.stdout+'\n'+result.stderr
    assert '1 passed' in result.stdout
