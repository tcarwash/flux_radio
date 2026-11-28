"""Offline-only Sotto SPI bench overlay and report parser tests."""
from pathlib import Path
import os
import struct
import subprocess
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
OVERLAY=ROOT/'firmware/k6-aura/apply-sotto-spi-bench.py'
HELPER=ROOT/'firmware/k6-aura/sotto-spi-bench.rs.inc'
sys.path.insert(0,str(ROOT/'tools'))
from k6_sotto_spi_bench import decode


def fixture(tmp_path):
    work=tmp_path/'aura'
    cps=work/'src/cps/mod.rs'
    nor=work/'src/drivers/norflash.rs'
    cps.parent.mkdir(parents=True)
    nor.parent.mkdir(parents=True)
    cps.write_text('''mod frame;
const HANDSHAKE: &[u8] = b"PROGRAMBF-K6AURA";
const CMD_CABLE_INFO: u8 = 0x56; // 'V'
fn cable_dispatch() {
  match cmd {
            frame::CMD_READ_FLASH_RAW if (f.addr as u32) < FLASH_CHIP_SIZE / FLASH_RAW_CHUNK => {
            }
  }
}
''')
    nor.write_text('pub fn read_bytes_stream_checked(&mut self) {}\n')
    return work,cps,nor


def run(work, enabled, success=True):
    p=subprocess.run([sys.executable,str(OVERLAY)],
      capture_output=True,text=True,timeout=10,env={
        **os.environ,'K6_RX4800_WORK':str(work),
        'K6_SOTTO_SPI_BENCH':str(int(enabled))})
    if success:assert p.returncode==0,p.stdout+p.stderr
    else:assert p.returncode!=0
    return p


def test_benchmark_overlay_enable_idempotent_and_restore(tmp_path):
    work,cps,nor=fixture(tmp_path)
    original=cps.read_bytes()
    run(work,True)
    enabled=cps.read_bytes()
    assert enabled!=original
    text=enabled.decode()
    assert text.count('fn sotto_spi_bench(')==1
    assert text.count('CMD_SOTTO_SPI_BENCH if f.addr == 0 && f.len == 0')==1
    assert '0x73' in text
    run(work,True)
    assert cps.read_bytes()==enabled
    run(work,False)
    assert cps.read_bytes()==original
    run(work,False)
    assert cps.read_bytes()==original
    run(work,True)
    assert cps.read_bytes()==enabled


def test_bench_requires_opt_in_streaming_path(tmp_path):
    work,cps,nor=fixture(tmp_path)
    nor.write_text('not streaming\n')
    before=cps.read_bytes()
    p=run(work,True,success=False)
    assert 'requires opted-in streaming' in p.stderr+p.stdout
    assert cps.read_bytes()==before


def test_bench_is_bound_read_only_and_timed_on_device():
    source=HELPER.read_text()
    assert 'const N: u16 = 128' in source
    assert '#[inline(never)]' in source  # comparison buffers get a dedicated bounded stack frame
    assert '[20usize, 128usize]' in source
    assert source.count('crate::hal::uptime::now()')==2
    assert 'read_bytes_checked(' in source
    assert 'read_bytes_stream_checked(' in source
    assert 'baseline != optimized' in source
    assert 'if hash[0] != hash[1]' in source
    assert 'SOTTO_SPI_BENCH_END' in source
    assert '0x100020' in source
    assert 'tx_on(' not in source
    assert 'tx_off(' not in source
    assert 'write_bytes(' not in source
    assert 'erase_' not in source
    assert 'mic_stream(' not in source


def test_decode_report_and_reject_corrupt():
    p=bytearray(24)
    p[:4]=b'SPB1'
    struct.pack_into('<HH4H2I',p,4,128,0,24,18,80,48,0x11,0x11)
    r=decode(bytes(p))
    assert r['valid']
    assert r['sample_count_per_path_per_size']==128
    assert r['read_paths']['20']['baseline_mean_us_per_read']==18.75
    assert r['read_paths']['20']['streaming_mean_us_per_read']==14.0625
    assert r['read_paths']['128']['estimated_savings_percent']==40.0
    struct.pack_into('<H',p,6,12)
    r=decode(bytes(p))
    assert not r['valid'] and r['digest_mismatch'] and r['spot_check_mismatch']
    with pytest.raises(ValueError):decode(bytes(p[:-1]))
    p[:4]=b'SPB0'
    with pytest.raises(ValueError):decode(bytes(p))
    p[:4]=b'SPB1'
    struct.pack_into('<H',p,4,0)
    with pytest.raises(ValueError):decode(bytes(p))


def test_cli_requires_explicit_reboot_acknowledgement():
    p=subprocess.run([sys.executable,str(ROOT/'tools/k6_sotto_spi_bench.py')],
                     capture_output=True,text=True,timeout=5)
    assert p.returncode!=0
    assert '--ack-reboot' in p.stderr
