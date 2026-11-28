"""Synthetic-ONLY API29 FSK protection gate source sanity."""
from pathlib import Path
import subprocess
import os
import sys
import pytest

SRC=Path(__file__).resolve().parents[1]/'firmware/k6-aura/apply-sotto-synth-host-ram0.py'

def test_only_explicit_synth_profile_can_rewrite_guards(tmp_path):
    p=tmp_path/'src/app/pager.rs';p.parent.mkdir(parents=True)
    l=tmp_path/'src/cps/live.rs';l.parent.mkdir(parents=True)
    pager='''static mut MOD_CTX: ModCtx = ModCtx {\nif new_tail && (!cfg!(k6_sotto_async_fsk) || !matches!(unsafe{*arena::base().add(12)},30|31)) {\nlet digital=cfg!(k6_sotto_async_fsk) && unsafe{*arena::base().add(12)>=30};\n'''
    p.write_text(pager);l.write_text('static mut CANCEL:CancelState=CancelState::empty();\n')
    env={**os.environ,'K6_RX4800_WORK':str(tmp_path),'K6_SOTTO_SYNTH_HOST':'1',
         'K6_RAM0':'1','K6_LIVE_SERIAL':'1','K6_SOTTO_MIC':'0'}
    result=subprocess.run([sys.executable,str(SRC)],env=env,capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    compiled=p.read_text();assert '29|30|31' in compiled and '>=29' in compiled
    assert compiled.count('link_section=".ram0"')==1
    assert l.read_text().count('link_section=".ram0"')==1
    no=tmp_path/'bad';(no/'src/app').mkdir(parents=True);(no/'src/cps').mkdir(parents=True)
    (no/'src/app/pager.rs').write_text('static mut MOD_CTX: ModCtx = ModCtx {\n')
    (no/'src/cps/live.rs').write_text('static mut CANCEL:CancelState=CancelState::empty();\n')
    env['K6_RX4800_WORK']=str(no)
    bad=subprocess.run([sys.executable,str(SRC)],env=env,capture_output=True,text=True)
    assert bad.returncode!=0
    assert (no/'src/app/pager.rs').read_text()=='static mut MOD_CTX: ModCtx = ModCtx {\n'

@pytest.mark.parametrize('synth,mic', [('0','0'),('1','1')])
def test_refuses_other_profiles(tmp_path,synth,mic):
    env={**os.environ,'K6_RX4800_WORK':str(tmp_path),'K6_SOTTO_SYNTH_HOST':synth,
         'K6_RAM0':'1','K6_LIVE_SERIAL':'1','K6_SOTTO_MIC':mic}
    result=subprocess.run([sys.executable,str(SRC)],env=env,capture_output=True,text=True)
    assert result.returncode!=0
