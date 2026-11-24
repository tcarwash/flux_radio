"""Build flag and repeated overlay safety for API30 PA11/TIM17 PCM service."""
import os
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]
OVERLAY=ROOT/"firmware/k6-aura/apply-sotto-api30-mic.py"

def apply(root):
    return subprocess.run(["python3",str(OVERLAY)],
            env={**os.environ,"K6_RX4800_WORK":str(root)},
            text=True,capture_output=True,timeout=20)

def test_overlay_requires_real_api29_base(tmp_path):
    pager=tmp_path/"src/app/pager.rs"
    pager.parent.mkdir(parents=True)
    pager.write_text("fn old_host() {}")
    r=apply(tmp_path)
    assert r.returncode!=0
    assert "requires enabled" in r.stderr
    assert pager.read_text()=="fn old_host() {}"

def test_mic_overlay_idempotent_does_not_shift_api_tail(tmp_path):
    src=tmp_path/"src"
    (src/"app").mkdir(parents=True)
    (src/"hal").mkdir()
    pager=src/"app/pager.rs"
    pager.write_text('''// SOTTO_API28_RESOURCES_BEGIN
// SOTTO_API29_ASYNC_FSK_BEGIN
static MOD_API: K6Api = K6Api {
    mic_stream: mod_sotto_mic_unavailable,
    fsk4800_rx: mod_fsk4800_rx,
};
unsafe extern "C" fn mod_tx_off() {
    unsafe{sotto_tx29_cleanup();}
}
fn run_module(app: &mut App, syst: &mut SYST) {
}
''')
    txdog=src/"hal/txdog.rs"
    txdog.write_text('''pub fn unkeyed() {
    KEYED_AT.store(0, Ordering::Relaxed);
    arm(ARM_TX, false);
}
''')
    hal=src/"hal/mod.rs"
    hal.write_text("pub mod txdog;\n")
    r=apply(tmp_path)
    assert r.returncode==0,r.stderr
    all_paths=(pager,txdog,hal,src/"hal/pcm_capture.rs")
    first=[x.read_bytes() for x in all_paths]
    assert "mic_stream: mod_sotto_mic_stream" in first[0].decode()
    assert "fsk4800_rx: mod_fsk4800_rx" in first[0].decode()
    assert "sotto_mic_stop();" in first[0].decode()
    assert "super::pcm_capture::stop();" in first[1].decode()
    assert "pub mod pcm_capture;" in first[2].decode()
    assert "fn isr()" in first[3].decode()
    again=apply(tmp_path)
    assert again.returncode==0,again.stderr
    assert [x.read_bytes() for x in all_paths]==first

def test_mic_host_is_off_by_default_and_requires_api29():
    shell=(ROOT/"firmware/k6-aura/build-fd6818b.sh").read_text()
    assert "K6_SOTTO_MIC=" in shell
    assert 'if [ "$K6_SOTTO_MIC" = 1 ] && [ "$K6_SOTTO_ASYNC_FSK" != 1 ]' in shell
    assert "apply-sotto-api30-mic.py" in shell
    assert "--cfg k6_sotto_mic" in shell
    source=(ROOT/"firmware/k6-aura/sotto-api30-mic.rs.inc").read_text()
    assert "n!=644" in source and "mod_tx_pa(0)" in source
    assert "sotto_mic_stop()" in source
    assert "read_ptt" in source
    assert "pcm_capture::start" in source
    assert "LIVE_ACTIVE.is_some()" in source
