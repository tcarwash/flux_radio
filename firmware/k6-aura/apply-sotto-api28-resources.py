#!/usr/bin/env python3
"""Opt-in real read-only Sotto API28 resource service after API31 overlay.

Preserves 26/27 KISS and 29/30 stub slots. Default firmware never runs this.
"""
from __future__ import annotations
import os
from pathlib import Path

root=Path(__file__).resolve().parent
work=Path(os.environ.get("K6_RX4800_WORK",root/"build/aura-fd6818b-port")).resolve()
pager=work/"src/app/pager.rs"
source=pager.read_text()

def swap_once(old,new):
    global source
    if new in source:return
    if source.count(old)!=1:
        raise SystemExit(f"Sotto resource overlay expected unique anchor {old[:90]!r}")
    source=source.replace(old,new,1)

if "// SOTTO_4800_RX_BEGIN" not in source:
    raise SystemExit("Sotto resource overlay requires existing API31 RX overlay")

swap_once("    resource_open: mod_sotto_resource_unavailable,",
          "    resource_open: mod_sotto_resource_open,")
swap_once("    resource_read: mod_sotto_read_unavailable,",
          "    resource_read: mod_sotto_resource_read,")
# A new module must never inherit the previous module's deck handle.
swap_once("fn run_module(app: &mut App, syst: &mut SYST) {\n",
          "fn run_module(app: &mut App, syst: &mut SYST) {\n"
          "    #[cfg(k6_sotto_resources)]\n"
          "    unsafe {SOTTO_RESOURCE_DECK=(0,0);}\n")
swap_once("    let exit_to_aura = unsafe { core::ptr::read_volatile(core::ptr::addr_of!(MOD_EXIT_TO_AURA)) };",
          "    #[cfg(k6_sotto_resources)]\n"
          "    unsafe {SOTTO_RESOURCE_DECK=(0,0);}\n"
          "    let exit_to_aura = unsafe { core::ptr::read_volatile(core::ptr::addr_of!(MOD_EXIT_TO_AURA)) };")
body=(root/"sotto-api28-resources.rs.inc").read_text()
if "// SOTTO_API28_RESOURCES_BEGIN" not in source:
    source+="\n"+body+"\n"
pager.write_text(source)
print("Applied opt-in API28 read-only Sotto resources")
