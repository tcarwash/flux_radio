#!/usr/bin/env python3
"""Install the opt-in, non-destructive Flux live serial dispatcher."""
from pathlib import Path

here = Path(__file__).resolve().parent
src = here / "build/aura-fd6818b-port/src"

def insert(path, old, new):
    data = path.read_text()
    if new in data:
        return
    if data.count(old) != 1:
        raise SystemExit(f"live serial: expected one anchor in {path}: {old!r}")
    path.write_text(data.replace(old, new, 1))

(src / "cps/live.rs").write_text((here / "live-serial.rs").read_text())
cps = src / "cps/mod.rs"
insert(cps, "mod frame;", 'mod frame;\n#[cfg(all(feature = "blowfang-host", k6_live_serial))]\npub mod live;')
insert(cps, "pub enum HostHandshake { Aura, Cable }",
       '''pub enum HostHandshake { Aura, Cable,
    #[cfg(k6_live_serial)]
    Live(live::Request),
}''')
insert(cps, "            if self.len >= HANDSHAKE.len()",
       '''            #[cfg(k6_live_serial)]
            if self.len >= live::REQUEST_LEN {
                if let Some(req) = live::decode(&self.tail[self.len - live::REQUEST_LEN..self.len]) {
                    self.len = 0;
                    return Some(HostHandshake::Live(req));
                }
            }
            if self.len >= HANDSHAKE.len()''')
main = src / "main.rs"
insert(main, "            Some(cps::HostHandshake::Aura) =>",
       '''            #[cfg(k6_live_serial)]
            Some(cps::HostHandshake::Live(req)) => {
                cps::live::dispatch(&mut app, &mut cp.SYST, req);
            }
            Some(cps::HostHandshake::Aura) =>''')
app = src / "app/mod.rs"
insert(app, "    pub fn master_index(&self) -> usize {",
       '''    #[cfg(k6_live_serial)]
    pub fn live_modulation(&self, side: usize) -> u8 {
        modulation_to_raw(self.sides[side].cfg.modulation)
    }

    // Deliberately volatile: no SPI writes on a CAT tune. Do not disturb
    // memories, reverse mode, or a resident digital engine.
    #[cfg(k6_live_serial)]
    pub fn live_tune(&mut self, syst: &mut SYST, hz: u32) -> u8 {
        if hz % 10 != 0 || !((136_000_000..=174_000_000).contains(&hz)
            || (400_000_000..=480_000_000).contains(&hz)) {
            return 2;
        }
        #[cfg(all(k6_vfo_probe, k6_vfo_spi_probe, k6_vfo_selector, not(k6_no_fsk_rx), feature = "blowfang-host"))]
        if self.resident_selected[self.master] || self.resident_selected[self.watching] { return 1; }
        let side = &mut self.sides[self.master];
        if side.vfo_chan != ChVfoMode::Vfo || side.reversed { return 1; }
        side.rx_freq_hz = hz;
        side.refresh_cfg_freqs();
        self.input.clear();
        self.sync_watching_to_master(syst);
        0
    }

    pub fn master_index(&self) -> usize {''')
