"""Compile and execute real CW module with mocked K6 API, no RF hardware.

Exercises PA gate and TX watchdog callback sequencing, abort latency,
clock wrapping, busy timeout and long strings without any actual radio.
"""
from __future__ import annotations
import shutil
import subprocess
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'tests/fixtures/k6_cw_carrier_mock.c'
MODULE=ROOT/'c/k6/mod/cwkey.c'


def _compile(tmp_path,*,extra=()):
    c=shutil.which('cc')
    if not c:pytest.skip('C toolchain unavailable')
    out=tmp_path/'cw_guard_mock'
    subprocess.run([c,'-std=c11','-O2','-Wall','-Wextra','-Werror',
                    '-I',str(ROOT/'c/k6/mod'),*extra,str(SOURCE),'-o',str(out)],
                   cwd=ROOT,check=True,text=True,capture_output=True,timeout=30)
    return out


def test_actual_cw_module_polls_key_and_guarantees_tx_off(tmp_path):
    exe=_compile(tmp_path)
    out=subprocess.run([str(exe)],check=True,text=True,capture_output=True,
                       timeout=10)
    for text in ('CLEAN','LEAD_CANCEL','MARK_CANCEL','MID_MARK_CANCEL',
                 'PA_ON_FAIL','PA_OFF_FAIL','TX_ON_REFUSED',
                 'CLOCK_WRAP_CLEAN','BUSY_TIMEOUT_CLEAN'):
        assert text in out.stdout,out.stdout
    assert 'unsafe_on=0' in out.stdout
    assert 't_us=200000' in out.stdout
    assert 't_us=430000' in out.stdout


def test_long_cw_text_is_rejected_before_any_pa_or_tx(tmp_path):
    exe=_compile(tmp_path,extra=(
        '-DCW_HARNESS_LONG_TEXT',
        '-DCW_TEXT="THIS IS A VERY LONG REPEATER CALLSIGN TEST"',
    ))
    out=subprocess.run([str(exe)],check=True,text=True,capture_output=True,
                       timeout=10)
    assert 'LONG_TEXT_REJECTED' in out.stdout


def test_cortex_m0_module_source_compiles_without_heap_or_abi_changes(tmp_path):
    clang=shutil.which('clang')
    if not clang:pytest.skip('clang not available')
    obj=tmp_path/'cwkey-m0.o'
    subprocess.run([clang,'--target=armv6m-none-eabi','-mcpu=cortex-m0',
                    '-mthumb','-ffreestanding','-Os','-std=c99','-Wall','-Wextra',
                    '-Werror','-fropi','-c',str(MODULE),'-o',str(obj)],
                   check=True,capture_output=True,text=True,timeout=30)
    assert obj.stat().st_size>0


def test_source_never_directly_controls_apc_or_calibration():
    src=MODULE.read_text()
    assert 'fd_write(' not in src
    assert 'cal_write(' not in src
    assert 'REG36' not in src and 'REG19' not in src
    assert 'api->tx_off()' in src
    assert 'CW_POLL_US 10000u' in src
