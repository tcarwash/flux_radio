"""RX-only issue40 new protocol; no physical radio accesses in these tests."""
from __future__ import annotations
import fcntl
import json
from pathlib import Path
import subprocess
import sys
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
import k6_tx27b_idle_bench as bench


def reports(word='9037',status='bff1'):
    return {'returned':True,'result':27,'reports':[
        f'tx27b 51a={word} 51b={word}',
        'tx27b 08=87ff 52=0292',
        'tx27b 70=0000 71=1234',
        f'tx27b 30a={status} 30b={status}']}


def test_preflight_compiles_exact_current_RX_only_thumb_source():
    meta,code,entry=bench.preflight()
    assert meta['issue']==40 and not meta['physical_rf']
    assert len(code)==meta['code_size']<=1600
    assert meta['transmitter_calls']==meta['RFIC_writes']==0
    assert 0<=entry<len(code)
    assert len(meta['source_sha256'])==64
    assert len(meta['module_code_sha256'])==64


def test_nonzero_reg51_is_valid_observation_not_false_error():
    for value in ('0000','9037','a03c','8000','0300'):
        d=bench.verify_report(reports(value))
        assert d['reg51']==int(value,16)
        assert d['reg51_nonzero'] is (value!='0000')
        assert d['reg51_hex']==value


@pytest.mark.parametrize('fault',[
    lambda d:d.update(result=-13),
    lambda d:d.update(returned=False),
    lambda d:d['reports'].pop(),
    lambda d:d['reports'].__setitem__(0,'tx27b 51a=9037 51b=0000'),
    lambda d:d['reports'].__setitem__(0,'non-hex'),
    lambda d:d['reports'].__setitem__(3,'tx27b 30a=bff3 30b=bff3'),
    lambda d:d['reports'].__setitem__(3,'tx27b 30a=bff1 30b=bff3'),
])
def test_report_parser_fails_closed_on_mismatch_or_tx_state(fault):
    data=reports()
    fault(data)
    with pytest.raises(bench.Refused):bench.verify_report(data)


def snapshot(slot=(),mode='FM',hz=433500000,tx=False):
    return {'status':{'vfos':[{'hz':hz,'mode':mode,'wide':True},
                              {'hz':144390000,'mode':'FM','wide':True}],
                      'transmitting':tx,'standby_idle':not tx},
            'blobs':{'0':{'size':400,'sha256':'a'*64},
                     **{str(i):{'size':200,'sha256':'b'*64} for i in slot}}}


def test_exact_packed_backup_and_vfo_and_tx_off_restoration():
    x=snapshot(slot=(1,2,31))
    bench.assert_idle_stable(x,x)
    mismatched=snapshot(slot=(1,2,31))
    mismatched['blobs']['2']['sha256']='c'*64
    with pytest.raises(bench.Refused,match='packed slots'):
        bench.assert_idle_stable(x,mismatched)
    with pytest.raises(bench.Refused,match='temporary slot'):
        bench.assert_idle_stable(x,snapshot(slot=(1,2,30,31)))
    with pytest.raises(bench.Refused,match='TX OFF'):
        bench.assert_idle_stable(x,snapshot(slot=(1,2,31),tx=True))
    with pytest.raises(bench.Refused,match='VFO'):
        bench.assert_idle_stable(x,snapshot(slot=(1,2,31),hz=440850000))


def test_operator_approval_and_issue_fresh_number_required(tmp_path):
    script=str(ROOT/'tools/k6_tx27b_idle_bench.py')
    python=sys.executable
    p=subprocess.run([python,script,'--preflight-only','--issue','20'],
                     text=True,capture_output=True,timeout=15)
    assert p.returncode!=0 and 'issue #40' in p.stderr
    q=subprocess.run([python,script,'--run','--issue','40','--prefix',str(tmp_path/'new')],
                     text=True,capture_output=True,timeout=15)
    assert q.returncode!=0 and 'operator approval' in q.stderr
    assert not (tmp_path/'new').exists()


def test_absent_fresh_prefix_refused_before_hardware():
    with pytest.raises(bench.Refused,match='fresh absolute'):
        bench.execute(Path('relative'),{},b'',0)


def test_source_guard_denies_future_tx_extensions(tmp_path):
    s=bench.SOURCE.read_text()
    for payload in ('api->fd_write(0x51,0);','api->tx_on(500);','api->tx_pa(1);'):
        file=tmp_path/'probe.c';file.write_text(s+'\n'+payload)
        with pytest.raises(bench.Refused):bench.source_guard(file)


def test_canonical_flock_is_nonblocking(tmp_path):
    path=tmp_path/'bench.lock'
    with path.open('a+b') as f:
        fcntl.flock(f.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(bench.Refused,match='held'):
            with bench.bench_lock(path):pass
        fcntl.flock(f.fileno(),fcntl.LOCK_UN)
    with bench.bench_lock(path):pass
