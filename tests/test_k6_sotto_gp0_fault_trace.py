"""Offline, fail-closed tests for the opt-in firmware GP0 fault trace."""
import os
from pathlib import Path
import struct
import subprocess
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
PATCHER=ROOT/'firmware/k6-aura/apply-sotto-gp0-fault.py'
sys.path.insert(0,str(ROOT/'tools'))
from k6_sotto_gp0_fault import decode,REPORT_SIZE

def packet(serial=1795730062,ret=-12,flags=0x23,
           resource_errors=0,fsk_errors=0,fsk_last=0,gp0_error=0,
           irq_count=3500,irq_peak=1100,missed=0):
    return b'GPF1'+struct.pack('<IiI2Ii4I',serial,ret,flags,
        resource_errors,fsk_errors,fsk_last,gp0_error,irq_count,
        irq_peak,missed)

def test_parse_unknown_engine_error():
    report=decode(packet())
    assert report['valid'] and report['failure_classes']==['module_or_engine_unknown']
    assert report['gp0_irq_count']==3500 and report['module_return']==-12

@pytest.mark.parametrize('flags,res_errors,fsk_errors,fsk_last,gp0_error,missed,expected',[
    (0x27,0,0,0,3,1,['pcm_dma']),
    (0x2b,1,0,0,0,0,['codec_resource']),
    (0x33,0,1,-2,0,0,['fsk_async']),
    (0x3f,1,1,-2,3,1,['pcm_dma','codec_resource','fsk_async']),
])
def test_classifies_independent_error_boundaries(flags,res_errors,fsk_errors,fsk_last,gp0_error,missed,expected):
    report=decode(packet(flags=flags,resource_errors=res_errors,
        fsk_errors=fsk_errors,fsk_last=fsk_last,gp0_error=gp0_error,missed=missed))
    assert report['failure_classes']==expected

def test_empty_boot_local_report():
    report=decode(b'GPF1'+bytes(40))
    assert not report['valid'] and not report['failure_classes']

def test_rejects_corrupt_and_inconsistent_reports():
    good=packet()
    for bad in (good[:-1],b'bad!'+good[4:],packet(flags=0xff),
                packet(flags=0x2b,resource_errors=0),
                packet(flags=0x33,fsk_errors=0,fsk_last=-2),
                packet(flags=0x20),b'GPF1'+bytes(36)+b'\0\0\0\1'):
        with pytest.raises(ValueError):
            decode(bad)
    assert len(good)==REPORT_SIZE==44

def fixture(tmp_path):
    work=tmp_path/'aura'
    files={
      'src/app/pager.rs':'''    resource_read: mod_sotto_resource_read,
    fsk_async: mod_sotto_async_fsk,
    mic_stream: mod_sotto_mic_stream,
    let ret = unsafe { ffi::km_run_on_stack(arena::base(), &MOD_API, module_stack_top()) };
    unsafe { mod_tx_off() }; // a module that left the transmitter on
unsafe extern "C" fn mod_sotto_resource_unavailable(_:u32)->i32 {-1}
''',
      'src/cps/mod.rs':'''fn handle() {
  match cmd {
            frame::CMD_READ_FLASH_RAW if (f.addr as u32) < FLASH_CHIP_SIZE / FLASH_RAW_CHUNK => {
            },
  }
}
''',
      'src/hal/gp0_capture.rs':'''pub fn active()->bool{false}
''',
    }
    for name,source in files.items():
        f=work/name
        f.parent.mkdir(parents=True,exist_ok=True)
        f.write_text(source)
    return work,files

def patch(work,enabled,success=True):
    result=subprocess.run([sys.executable,str(PATCHER)],capture_output=True,
        text=True,timeout=10,env={**os.environ,'K6_RX4800_WORK':str(work),
                                  'K6_SOTTO_GP0_TRACE':str(int(enabled))})
    if success:assert result.returncode==0,result.stdout+result.stderr
    else:assert result.returncode!=0
    return result

def test_overlay_exact_restore_idempotence_and_read_only(tmp_path):
    work,files=fixture(tmp_path)
    patch(work,True)
    enabled={name:(work/name).read_text() for name in files}
    assert 'resource_read: mod_sotto_resource_read_traced' in enabled['src/app/pager.rs']
    assert 'gp0_fault_trace_finish(ret)' in enabled['src/app/pager.rs']
    assert '0x75 if f.addr == 0 && f.len == 0' in enabled['src/cps/mod.rs']
    assert 'fault_ring_lost' in enabled['src/hal/gp0_capture.rs']
    assert 'gp0_fault_trace_report()' in enabled['src/cps/mod.rs']
    patch(work,True)
    assert {name:(work/name).read_text() for name in files}==enabled
    patch(work,False)
    assert {name:(work/name).read_text() for name in files}==files
    patch(work,False)
    assert {name:(work/name).read_text() for name in files}==files

def test_overlay_fails_closed_if_api_contract_missing(tmp_path):
    work,files=fixture(tmp_path)
    p=work/'src/app/pager.rs'
    p.write_text(p.read_text().replace('resource_read: mod_sotto_resource_read,','resource_read: unexpected,'))
    before={name:(work/name).read_bytes() for name in files}
    error=patch(work,True,success=False)
    assert 'API30 field triplet' in error.stderr+error.stdout
    assert {name:(work/name).read_bytes() for name in files}==before

def test_trace_arms_on_executable_after_signed_k6m_envelope():
    # KUL2 loads the complete signed K6M envelope into the arena. Its
    # executable K6NM name is at +16, not at the envelope base.
    from modem import k6mod
    from tools.k6_sotto_module import build
    code,entry=build(host_resources=True,gp0_irq_tx=True)
    assert entry==16 and code[:13]==b"K6NMSOTTO GP0"
    envelope=k6mod.image(code,entry,1795730062,None,level=30,lib=24)
    assert envelope[:4]==b"K6M\x01"
    assert envelope[:13]!=b"K6NMSOTTO GP0"
    assert envelope[16:29]==b"K6NMSOTTO GP0"
    patcher=(ROOT/'firmware/k6-aura/apply-sotto-gp0-fault.py').read_text()
    assert 'gp0_fault_trace_begin(st.serial, unsafe { arena::base().add(16) });' in patcher
    assert 'gp0_fault_trace_begin(st.serial, arena::base());' not in patcher

def test_tracing_does_not_change_original_gp0_module_source():
    source=(ROOT/'c/k6/mod/sotto_gp0_irq_tx.c').read_text()
    assert 'if(r<0){error="GP0 ENGINE";goto fail;}' in source
    helper=(ROOT/'firmware/k6-aura/sotto-gp0-fault.rs.inc').read_text()
    assert 'fn mod_sotto_async_fsk_traced' in helper
    assert 'fn mod_sotto_resource_read_traced' in helper
    assert 'fn mod_sotto_mic_stream_traced' in helper
    assert 'pub(crate) fn gp0_fault_trace_report' in helper
    assert 'write_bytes' not in helper and 'tx_on(' not in helper
