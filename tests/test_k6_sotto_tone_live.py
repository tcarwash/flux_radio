"""Offline-only smoke/guard tests for the opt-in Sotto tone live controller."""
from __future__ import annotations
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from tools import k6_sotto_tone_live as tone


def test_cli_rejects_rf_without_dummy_load_even_if_serial_unavailable(tmp_path):
    args=SimpleNamespace(dummy_load_confirmed=False,slot=24,port="/dev/ttyDoesNotExist",
                         monitor_url="http://127.0.0.1:1",capture_dir=tmp_path/"capture")
    with pytest.raises(ValueError,match="dummy-load"):
        tone.live_run(args)
    assert not (tmp_path/"capture").exists()


def test_cli_rejects_install_without_explicit_authorization(tmp_path):
    args=SimpleNamespace(confirm_install=False,slot=24,cable_only=True,key_file=None,
                         port="/dev/ttyDoesNotExist",envelope_out=None)
    with pytest.raises(ValueError,match="confirm-install"):
        tone.live_install(args)


def test_radio_preflight_requires_selected_vfo_and_idle():
    class Radio:
        def __init__(self,hz=433_500_000,idle=True):
            self.hz,self.idle=hz,idle
        def status(self):
            return dict(transmitting=False,standby_idle=self.idle,master=0,
                        vfos=[dict(hz=self.hz),dict(hz=433_500_000)])
        def capabilities(self):
            return dict(features=["run","coop_cancel","module_caps"])
    tone.radio_preflight(Radio())
    with pytest.raises(RuntimeError,match="Active VFO"):
        tone.radio_preflight(Radio(hz=433_510_000))
    with pytest.raises(RuntimeError,match="idle RX"):
        tone.radio_preflight(Radio(idle=False))


def test_builds_tone_binary_and_manifest_without_radio(tmp_path,monkeypatch):
    import subprocess
    import sys
    out=tmp_path/"tone"
    p=subprocess.run([sys.executable,str(Path(tone.__file__)),"build","--out",str(out)],
                     capture_output=True,text=True,timeout=60)
    assert p.returncode==0,p.stdout+p.stderr
    d=json.loads((out/"manifest.json").read_text())
    assert d["name"]=="SOTTO TONE" and d["level"]==31
    assert d["module_bytes"]==(out/"module.bin").stat().st_size
    assert d["module_bytes"]>5000
    assert "433.500 MHz" in d["tx_policy"]
