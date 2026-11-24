"""Compile-only ABI31/overlay gates for experimental FD6818B receiver."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest

ROOT=Path(__file__).resolve().parents[1]
HEADER=ROOT/'c/k6/k6_mod.h'
OVERLAY=ROOT/'firmware/k6-aura/apply-sotto-rx4800.py'
SCRIPT=ROOT/'firmware/k6-aura/build-fd6818b.sh'
RS_BODY=ROOT/'firmware/k6-aura/sotto-fsk4800-rx.rs.inc'


def test_append_only_api31_c_offsets(tmp_path):
    c=tmp_path/'probe.c'
    c.write_text('''#include <stddef.h>
#include "c/k6/k6_mod.h"
_Static_assert(offsetof(k6_api_t, fsk4800_rx)>offsetof(k6_api_t,mic_stream),"API31 moved");
_Static_assert(offsetof(k6_api_t,mic_stream)>offsetof(k6_api_t,fsk_async),"API30 moved");
_Static_assert(offsetof(k6_api_t,fsk_async)>offsetof(k6_api_t,resource_read),"API29 moved");
_Static_assert(offsetof(k6_api_t,resource_open)>offsetof(k6_api_t,publish_rx),"API28 moved");
_Static_assert(offsetof(k6_api_t,mb_version)>offsetof(k6_api_t,mode_info),"KISS moved");
int main(void){return 0;}
''')
    r=subprocess.run(['cc','-std=c11','-Werror','-fsyntax-only',
                      '-I',str(ROOT),str(c)],capture_output=True,text=True,timeout=25)
    assert r.returncode==0,r.stderr


def test_generated_overlay_is_append_only_and_idempotent(tmp_path):
    pager=tmp_path/'src/app/pager.rs'
    pager.parent.mkdir(parents=True)
    pager.write_text('''pub struct K6Api {
        pub publish_rx: unsafe extern "C" fn(u16, *const u8, u16) -> i32,
}
static MOD_API: K6Api = K6Api {
    publish_rx: mod_mb_publish_rx,
    level: if cfg!(k6_packet_mailbox) { 27 } else if cfg!(k6_mode_registry) { 26 } else { 25 },
};
unsafe extern "C" fn mod_tx_off() {}
unsafe extern "C" fn mod_tx_on(max_ms:u32) -> i32 {
    let ms = max_ms.clamp(1, MOD_TX_MAX_MS);
    return 0;
}
''')
    env={**os.environ,'K6_RX4800_WORK':str(tmp_path)}
    def run():
        return subprocess.run(['python3',str(OVERLAY)],env=env,
                              capture_output=True,text=True,timeout=20)
    out=run()
    assert out.returncode==0,out.stderr
    first=pager.read_text()
    assert 'level: if cfg!(k6_fsk4800_rx) { 31 } else if cfg!(k6_packet_mailbox) { 27 }' in first
    assert first.index('pub publish_rx')<first.index('pub resource_open')<first.index('pub resource_read')<first.index('pub fsk_async')<first.index('pub mic_stream')<first.index('pub fsk4800_rx')
    assert first.index('publish_rx: mod_mb_publish_rx')<first.index('resource_open: mod_sotto_resource_unavailable')<first.index('fsk4800_rx: mod_fsk4800_rx')
    assert 'return -1;' in first
    assert 'if unsafe {*arena::base().add(12) >= 28} {return -1;}' in first
    assert first.count('// SOTTO_4800_RX_BEGIN')==1
    assert run().returncode==0
    assert pager.read_text()==first


def test_overlay_refuses_missing_mailbox_or_unexpected_rust(tmp_path):
    pager=tmp_path/'src/app/pager.rs'
    pager.parent.mkdir(parents=True)
    pager.write_text('''pub struct K6Api {
 pub mode_info: u32,
}''')
    run=subprocess.run(['python3',str(OVERLAY)],
                       env={**os.environ,'K6_RX4800_WORK':str(tmp_path)},
                       capture_output=True,text=True,timeout=20)
    assert run.returncode!=0
    assert 'unique anchor' in run.stderr
    assert pager.read_text().startswith('pub struct K6Api')


def test_build_requires_api27_and_data_with_watchdog():
    shell=SCRIPT.read_text()
    rx=RS_BODY.read_text()
    assert 'K6_FSK4800_RX=${K6_FSK4800_RX:-0}' in shell
    assert 'K6_MODE_REGISTRY' in shell and 'K6_PACKET_MAILBOX' in shell
    assert 'K6_LEAN_TXDOG' in shell and 'K6_FSK_RX_SERVICE' in shell
    assert 'apply-sotto-rx4800.py' in shell
    assert 'k6_fsk4800_rx' in shell
    assert 'K6_AURA_RESET_GENERATED' in shell
    assert 'git -C "$WORK" reset --hard "$REV"' in shell
    assert 'git -C "$WORK" clean -fd' in shell
    assert 'mod_tx_pa(' not in rx and 'pa_enable(' not in rx
    assert 'async_fsk' not in rx
    assert 'RX4800_DEADLINE' in rx
    assert 'RX4800_FRAME_READY' in rx
    assert 'fd.reg_write(syst,0x59,0x40f8)' in rx
    assert 'RX4800_FRAME_READY=true' in rx
    assert 'rx4800_stop();' in rx
    assert 'REG_5E' not in rx or '0x5e' in rx
    assert '0x2000' in rx and '0x1000' in rx


def test_existing_api24_rejects_4800_rx():
    s=(ROOT/'firmware/k6-aura/apply-configurable-fsk.py').read_text()
    assert 'if fast { if op != 2 || n != 33 || gain != 21 {return -1;} }' in s
    assert 'No 4800 RX' in HEADER.read_text()
