"""Compile/build-overlay safety for experimental Sotto API28 host resources."""
from pathlib import Path
import os
import subprocess

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/"firmware/k6-aura/apply-sotto-api28-resources.py"
BUILD=ROOT/"firmware/k6-aura/build-fd6818b.sh"
SOURCE=ROOT/"firmware/k6-aura/sotto-api28-resources.rs.inc"

def _run(tmp):
    return subprocess.run(["python3",str(SCRIPT)],
                          env={**os.environ,"K6_RX4800_WORK":str(tmp)},
                          capture_output=True,text=True,timeout=20)

def test_opt_in_resource_overlay_is_idempotent_preserves_older_slots(tmp_path):
    pager=tmp_path/"src/app/pager.rs"
    pager.parent.mkdir(parents=True)
    pager.write_text('''// SOTTO_4800_RX_BEGIN
static MOD_API: K6Api = K6Api {
    resource_open: mod_sotto_resource_unavailable,
    resource_read: mod_sotto_read_unavailable,
    fsk_async: mod_sotto_async_unavailable,
    mic_stream: mod_sotto_mic_unavailable,
};
fn run_module(app: &mut App, syst: &mut SYST) {
    let st = unsafe { &*ffi::km_status() };
    let exit_to_aura = unsafe { core::ptr::read_volatile(core::ptr::addr_of!(MOD_EXIT_TO_AURA)) };
}
''')
    out=_run(tmp_path)
    assert out.returncode==0,out.stderr
    once=pager.read_text()
    assert "resource_open: mod_sotto_resource_open," in once
    assert "resource_read: mod_sotto_resource_read," in once
    assert "fsk_async: mod_sotto_async_unavailable," in once
    assert "mic_stream: mod_sotto_mic_unavailable," in once
    assert once.count("SOTTO_RESOURCE_DECK=(0,0);")>=3
    assert once.count("// SOTTO_API28_RESOURCES_BEGIN")==1
    assert _run(tmp_path).returncode==0
    assert pager.read_text()==once

def test_resource_overlay_refuses_non_api31_generated_checkout(tmp_path):
    pager=tmp_path/"src/app/pager.rs"
    pager.parent.mkdir(parents=True)
    pager.write_text("fn main(){}")
    r=_run(tmp_path)
    assert r.returncode!=0
    assert "requires existing API31" in r.stderr
    assert pager.read_text()=="fn main(){}"

def test_resource_callback_bounds_and_actual_build_flag_are_opt_in():
    rust=SOURCE.read_text()
    sh=BUILD.read_text()
    assert 'if [ "$K6_SOTTO_RESOURCES" = 1 ] && [ "$K6_FSK4800_RX" != 1 ]' in sh
    assert 'apply-sotto-api28-resources.py' in sh
    assert '--cfg k6_sotto_resources' in sh
    assert "resource_open" in rust and "resource_read" in rust
    assert "slot>=32" in rust and "extent < SOTTO_K6SC_LEN + 24" in rust
    assert "n==0 || n>256" in rust
    assert "n>len-off" in rust and "n>0x200000-addr" in rust
    assert "start<0x20000000" in rust and "end>0x20006000" in rust
    assert "read_bytes_checked" in rust
    assert "write_bytes(" not in rust and "erase_sector(" not in rust
    assert "pa_enable" not in rust and "fd.reg_write" not in rust
