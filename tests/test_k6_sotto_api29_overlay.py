"""Guard the experimental PA-muted API29 host overlay and its prerequisites."""
import os
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]
OVERLAY=ROOT/"firmware/k6-aura/apply-sotto-api29-async-fsk.py"

def apply(tmp):
    return subprocess.run(["python3",str(OVERLAY)],
        env={**os.environ,"K6_RX4800_WORK":str(tmp)},
        capture_output=True,text=True,timeout=15)

def test_overlay_refuses_host_without_api28_and_api31(tmp_path):
    p=tmp_path/"src/app/pager.rs"
    p.parent.mkdir(parents=True)
    p.write_text("fn test_only() {}")
    result=apply(tmp_path)
    assert result.returncode!=0
    assert "requires already-applied" in result.stderr
    assert p.read_text()=="fn test_only() {}"

def test_async_overlay_is_idempotent_and_preserves_abi_and_analog_tx(tmp_path):
    p=tmp_path/"src/app/pager.rs"
    p.parent.mkdir(parents=True)
    p.write_text('''// SOTTO_4800_RX_BEGIN
// SOTTO_API28_RESOURCES_BEGIN
static MOD_API: K6Api = K6Api {
    fsk_async: mod_sotto_async_unavailable,
};
unsafe extern "C" fn mod_tx_on(max_ms: u32) -> i32 {
        // RX-only API31 is not a full API28-30 voice transmitter host.
        // Reject new-tail modules' TX before any analog PA transition.
        if unsafe {*arena::base().add(12) >= 28} {return -1;}
        unsafe {rx4800_stop();}
    let ms = max_ms.clamp(1, MOD_TX_MAX_MS);
    app.radio.set_tx_allowed(BandLock::Fcc.tx_ranges());
    let ok = app.radio.enter_tx(syst);
}
unsafe extern "C" fn mod_tx_off() {
}
fn run_module(app: &mut App, syst: &mut SYST) {
}
''')
    radio=tmp_path/"src/device/radio.rs"
    radio.parent.mkdir(parents=True,exist_ok=True)
    radio.write_text('''    pub fn enter_tx(&mut self, syst: &mut SYST) -> bool {
        if !self.tx_allowed.allows(self.cfg.tx_freq_hz) {return false;}
        self.fd6818.pa_enable(syst, self.cfg.power);
        true
    }
    /// Bare-carrier TX for CW/CWF keying:
''')
    first=apply(tmp_path)
    assert first.returncode==0,first.stderr
    initial=p.read_text(),radio.read_text()
    assert initial[0].count("// SOTTO_API29_ASYNC_FSK_BEGIN")==1
    assert "fsk_async: mod_sotto_async_fsk," in initial[0]
    assert "k6_sotto_async_fsk" in initial[0]
    assert "resource_read:" not in initial[0]
    assert "app.radio.enter_tx_with_pa(syst,!digital)" in initial[0]
    assert "if pa {self.fd6818.pa_enable" in initial[1]
    assert "else {self.fd6818.pa_off(syst);}" in initial[1]
    assert "self.enter_tx_with_pa(syst,true)" in initial[1]
    second=apply(tmp_path)
    assert second.returncode==0,second.stderr
    assert (p.read_text(),radio.read_text())==initial

def test_host_build_opt_in_requires_resource_and_api31():
    shell=(ROOT/"firmware/k6-aura/build-fd6818b.sh").read_text()
    body=(ROOT/"firmware/k6-aura/sotto-api29-async-fsk.rs.inc").read_text()
    assert 'K6_SOTTO_ASYNC_FSK=' in shell
    assert 'K6_SOTTO_RESOURCES" != 1' in shell
    assert 'apply-sotto-api29-async-fsk.py' in shell
    assert '--cfg k6_sotto_async_fsk' in shell
    assert 'n!=33' in body and '0xa55a' in body
    assert "SOTTO_TX29_UNTIL=mod_tick().wrapping_add(7000)" in body
    assert 'mod_tx_off(); -2' in body
    assert 'sotto_tx29_cleanup()' in body
    assert 'mod_fd_read(0)!=0x4829' in body
