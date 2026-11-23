"""Offline radio operator configuration preservation checks; no hardware I/O."""
from copy import deepcopy
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from k6_fsk_boundary_bench import (vfo_baseline_matches, INITIAL_HZ, SECONDARY_HZ)


def _baseline():
    return dict(watching=0,transmitting=False,standby_idle=True,
                vfos=[dict(hz=440850000,mode='FM',wide=True,memory=False),
                      dict(hz=144390000,mode='FM',wide=True,memory=False)])


def test_exact_distinct_vfo_configuration_is_recognized():
    assert INITIAL_HZ==440850000 and SECONDARY_HZ==144390000
    assert vfo_baseline_matches(_baseline())


def test_prior_both_144390_frequency_assumption_is_not_allowed():
    state=_baseline()
    state['vfos'][0]['hz']=144390000
    assert not vfo_baseline_matches(state)


def test_reject_unexpected_changes_and_different_watched_vfo():
    baseline=_baseline()
    for path,value in [('watching',1),('mode','AM'),('wide',False),('memory',True),('hz',144391000)]:
        state=deepcopy(baseline)
        if path=='watching':state[path]=value
        else:state['vfos'][0][path]=value
        assert not vfo_baseline_matches(state),path
    for path,value in [('mode','AM'),('wide',False),('memory',True),('hz',144391000)]:
        state=deepcopy(baseline)
        state['vfos'][1][path]=value
        assert not vfo_baseline_matches(state),path


def test_radio_status_missing_fields_fails_closed():
    for status in ({},{'vfos':[]},{'watching':0,'vfos':[{'hz':440850000}]},
                   {'watching':0,'vfos':[{'hz':440850000},{'hz':144390000}]}):
        assert not vfo_baseline_matches(status)
