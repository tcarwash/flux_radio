"""Benchkeeper safety and scoring. Real code paths exercised separately in integration."""
import pytest
from tools.k6_sotto_opt_bench import parse_case,tally,mixed_pcm

def test_case_limits():
    assert parse_case('4800:34')==(4800,34)
    for bad in ('', '4800', '-1:34','300:0','12001:10','30:201','x:20','20:3:4'):
        with pytest.raises(Exception):parse_case(bad)

def test_score_no_regression_and_recovery():
    x=tally([({'accepted':True},{'accepted':True}),
             ({'accepted':False},{'accepted':True}),
             ({'accepted':False},{'accepted':False})])
    assert x==dict(trials=3,baseline_pass=1,candidate_pass=2,improved=1,regressed=0,no_regressions=True)
    y=tally([({'accepted':True},{'accepted':False})])
    assert y['regressed']==1 and not y['no_regressions']
    with pytest.raises(ValueError):tally([])


def test_deterministic_mixed_fixture():
    a=mixed_pcm();b=mixed_pcm()
    assert a.dtype.str == "<i2" and len(a)==9600
    assert a.tobytes()==b.tobytes()
    assert not a[:1600].any() and a[3000] != 0
