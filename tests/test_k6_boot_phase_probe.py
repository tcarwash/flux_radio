"""The opt-in GP0 boot tracer survives the iterative boot UI overlay pass."""
import os
from pathlib import Path
import subprocess
import sys

PROBE = Path(__file__).resolve().parents[1] / 'firmware/k6-aura/apply-boot-phase-probe.py'
MAIN = '''fn main() {
    app.radio_mut().enter_rx(&mut cp.SYST);
    loop {
        let due = scheduler.tick();
            app.poll_blink();
            draw_ui_on_arena(&mut display, &mut app);
    }
}
'''
BOOT = 'fn draw_headlines<D>(lcd: &mut D, l1: &str, l2: &str) {}\n'
UART = '''pub unsafe extern "C" fn USART1() {
    let regs = &*kd32f328_pac::Usart1::ptr();
    if isr.rxne().bit_is_set() {
        let byte = regs.rdr().read().rdr().bits() as u8;
    }
}
'''



def apply(work):
    env = dict(os.environ, K6_BOOT_PHASE_WORK=str(work))
    return subprocess.run([sys.executable, str(PROBE)], env=env, capture_output=True, text=True)


def test_probe_idempotent_and_survives_boot_ui_regeneration(tmp_path):
    (tmp_path / 'src/ui').mkdir(parents=True)
    (tmp_path / 'src/hal').mkdir(parents=True)
    (tmp_path / 'src/hal/uart.rs').write_text(UART)
    main = tmp_path / 'src/main.rs'
    boot = tmp_path / 'src/ui/boot.rs'
    main.write_text(MAIN)
    boot.write_text(BOOT)
    assert apply(tmp_path).returncode == 0
    first = main.read_text()
    assert 'IRQ YES' in first and 'RX YES' in first
    assert 'UART_PHASE_PROBE_BEGIN' in (tmp_path / 'src/hal/uart.rs').read_text()
    assert all(first.count(f'"PHASE {n}"') == 1 for n in (1, 2, 3))
    assert apply(tmp_path).returncode == 0
    assert main.read_text() == first
    assert boot.read_text().count('BOOT_PHASE_PROBE_BEGIN') == 1
    # Size-branding/raster overlays may regenerate the boot UI between relinks.
    boot.write_text(BOOT)
    assert apply(tmp_path).returncode == 0
    assert boot.read_text().count('BOOT_PHASE_PROBE_BEGIN') == 1
    assert main.read_text() == first


def test_probe_fails_without_expected_rx_entry(tmp_path):
    (tmp_path / 'src/ui').mkdir(parents=True)
    (tmp_path / 'src/hal').mkdir(parents=True)
    (tmp_path / 'src/hal/uart.rs').write_text(UART)
    (tmp_path / 'src/main.rs').write_text('fn main() {}')
    (tmp_path / 'src/ui/boot.rs').write_text(BOOT)
    result = apply(tmp_path)
    assert result.returncode != 0
    assert 'normal RX entry expected once' in result.stderr
