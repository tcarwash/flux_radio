#!/usr/bin/env python3
"""Experimental best-effort EL power, stored as raw value 3 (FD6818B only)."""
from pathlib import Path
root=Path(__file__).parent/'build/aura-fd6818b-port/src'
def patch(name, pairs):
 p=root/name;s=p.read_text()
 for old,new in pairs:
  if new in s: continue
  if old not in s: raise SystemExit(f'{name}: missing {old}')
  s=s.replace(old,new,1)
 p.write_text(s)
patch('drivers/fd6818b.rs',[('    High,\n}', '    High,\n    #[cfg(k6_el_power)]\n    ExtraLow,\n}')])
patch('app/convert.rs',[('        2 => Power::Low,','        #[cfg(k6_el_power)]\n        3 => Power::ExtraLow,\n        2 => Power::Low,'),('        Power::Low => 2,','        Power::Low => 2,\n        #[cfg(k6_el_power)]\n        Power::ExtraLow => 3,')])
for name in ['app/settings_ops.rs','app/chanmgr.rs','ui/standby.rs']:
 patch(name,[('Power::Low => "LOW",','Power::Low => "LOW",\n                    #[cfg(k6_el_power)]\n                    Power::ExtraLow => "EL",')])
patch('ui/standby.rs',[('Power::Low => 2,','Power::Low => 2,\n        #[cfg(k6_el_power)]\n        Power::ExtraLow => 1,')])
patch('app/settings_ops.rs',[('SettingItem::TxPr | SettingItem::Roge => clamp_step(cur, up, 0, 2),','SettingItem::TxPr => clamp_step(cur, up, 0, if cfg!(k6_el_power) {3} else {2}),\n        SettingItem::Roge => clamp_step(cur, up, 0, 2),')])
patch('app/chanmgr.rs',[('Field::Power => clamp_step(cur, up, 0, 2),','Field::Power => clamp_step(cur, up, 0, if cfg!(k6_el_power) {3} else {2}),')])
patch('app/side_ops.rs',[('Power::High => Power::Low,','#[cfg(not(k6_el_power))]\n        Power::High => Power::Low,\n        #[cfg(k6_el_power)]\n        Power::High => Power::ExtraLow,\n        #[cfg(k6_el_power)]\n        Power::ExtraLow => Power::Low,')])
patch('device/storage.rs',[('Power::Low => addr::PA_TABLE_BASE_LOW,','Power::Low => addr::PA_TABLE_BASE_LOW,\n            #[cfg(k6_el_power)]\n            Power::ExtraLow => addr::PA_TABLE_BASE_LOW,'),('Some(self.read_pa_byte(addr))','let target = self.read_pa_byte(addr);\n        #[cfg(k6_el_power)]\n        if power == Power::ExtraLow {\n            // Best effort: 7/16 of Low APC. Use conservative raw fallback on blank calibration.\n            return Some(if target == 0 || target == 255 { 72 } else { ((target as u16 * 7) / 16) as u8 });\n        }\n        Some(target)')])
