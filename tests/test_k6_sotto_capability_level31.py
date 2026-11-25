"""Capability recorder advertises the C loader's opt-in API ceiling."""
from pathlib import Path
import os
import re
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/'firmware/k6-aura/apply-capability-query.py'

def _record(tmp_path, overrides):
    build=tmp_path/'firmware/build/aura-fd6818b-port'
    target=build/'src/cps/mod.rs'
    target.parent.mkdir(parents=True)
    target.write_text('const CMD_CABLE_INFO: u8 = 0x56;\n            CMD_CABLE_INFO => {\n')
    (build/'build.rs').write_text('    if feat("VOICE_EXPLORE") {\n        File::create(out.join("k6id.x"))')
    script=tmp_path/'firmware/apply-capability-query.py'
    shutil.copy2(SCRIPT,script)
    env={**os.environ,'K6_FSK_RX_SERVICE':'1','K6_SCAN_SERVICE':'0','K6_OTA_SERVICE':'0','K6_LIVE_SERIAL':'1',
         'K6_MODE_REGISTRY':'0','K6_PACKET_MAILBOX':'0','K6_FSK4800_RX':'0',**overrides}
    cp=subprocess.run([sys.executable,str(script)],env=env,capture_output=True,text=True)
    output=target.read_text()
    level_match=re.search(r"\[b'K', b'6', b'C', b'P', 2, (\d+), (\d+), 0",output)
    return cp,tuple(map(int,level_match.groups())) if level_match else None

def test_api31_capability(tmp_path):
    cp,record=_record(tmp_path,dict(K6_MODE_REGISTRY='1',K6_PACKET_MAILBOX='1',K6_FSK4800_RX='1'))
    assert cp.returncode==0,cp.stderr
    assert record[1]==31

def test_api27_without_rx(tmp_path):
    cp,record=_record(tmp_path,dict(K6_MODE_REGISTRY='1',K6_PACKET_MAILBOX='1'))
    assert cp.returncode==0,cp.stderr
    assert record[1]==27

def test_legacy_api25_unchanged(tmp_path):
    cp,record=_record(tmp_path,{})
    assert cp.returncode==0,cp.stderr
    assert record[1]==25

def test_api31_requires_registry_and_mailbox(tmp_path):
    cp,record=_record(tmp_path,dict(K6_FSK4800_RX='1'))
    assert cp.returncode!=0
    assert record is None
