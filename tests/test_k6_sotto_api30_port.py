"""Compile-only API28-30 tail safety and actual experimental Sotto TX module."""
from __future__ import annotations

from pathlib import Path
import re
import subprocess
import pytest

ROOT=Path(__file__).resolve().parents[1]
HEADER=ROOT/'c/k6/k6_mod.h'
SRC=ROOT/'c/k6/mod/sotto_live_tx.c'


def test_api28_30_are_append_only_after_kiss_mailbox(tmp_path):
    source='''#include "c/k6/k6_mod.h"
#include <stddef.h>
_Static_assert(offsetof(k6_api_t, mode_info)>offsetof(k6_api_t,cancel_poll),"mode info moved");
_Static_assert(offsetof(k6_api_t,mb_version)>offsetof(k6_api_t,mode_info),"mailbox moved");
_Static_assert(offsetof(k6_api_t,publish_rx)>offsetof(k6_api_t,mb_version),"mailbox field moved");
_Static_assert(offsetof(k6_api_t,resource_open)>offsetof(k6_api_t,publish_rx),"Sotto resource tail not append-only");
_Static_assert(offsetof(k6_api_t,resource_read)>offsetof(k6_api_t,resource_open),"resource order");
_Static_assert(offsetof(k6_api_t,fsk_async)>offsetof(k6_api_t,resource_read),"FSK order");
_Static_assert(offsetof(k6_api_t,mic_stream)>offsetof(k6_api_t,fsk_async),"mic order");
int main(void){return 0;}
'''
    c=tmp_path/'probe.c';c.write_text(source)
    p=subprocess.run(['cc','-std=c11','-Werror','-fsyntax-only','-I',str(ROOT),str(c)],
                     capture_output=True,text=True,timeout=20)
    assert p.returncode==0,p.stderr
    h=HEADER.read_text()
    assert h.index('(*mode_info)')<h.index('uint32_t mb_version;')<h.index('(*publish_rx)')<h.index('(*resource_open)')<h.index('(*fsk_async)')<h.index('(*mic_stream)')


def test_ported_live_module_never_runs_against_old_api():
    s=SRC.read_text()
    assert '#define K6_MODULE_LEVEL 30' in s
    assert 'a->level<30' in s
    assert 'a->fsk_async(0,p,33)' in s
    assert 'a->mic_stream(0,&s->mic,sizeof(s->mic))' in s
    assert 'a->tx_on(25000u)' in s
    assert 'a->tx_off()' in s
    assert 'K6_MODULE_NAME("SOTTO MIC")' in s


def test_real_module_emits_api30_and_size(tmp_path):
    from tools.k6_sotto_module import build
    code,entry=build(host_resources=True,live_tx=True)
    assert code
    assert 9500<=len(code)<=10216
    assert 0<=entry<len(code)
    # On disk original module header indexes stay intact; new image runs
    # only on a new API30 host with added tail and support services.
    assert b'SOTTO MIC' in code
    with pytest.raises(ValueError):
        build(live_tx=True)


def test_attended_catalog_entry_distinct_from_legacy_and_early_preflight_visible():
    src=SRC.read_text()
    assert 'K6_MODULE_NAME("SOTTO MIC")' in src
    assert src.index('a->lcd_text(0,"SOTTO MIC")') < src.index('a->scratch(&room)')
    assert 'CODEC DATA ERROR' in src
    assert 'a->delay_us(1000000)' in src
    assert 'SOTTO MIC BENCH' in src
