"""The original GP0 PCM AIR receive CLI failed on tools.* imports; prevent recurrence."""
from pathlib import Path
import subprocess
import sys
import pytest

ROOT=Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('script',[
    'k6_gp0_noise_fingerprint.py',
    'k6_gp0_noise_filter_model.py',
    'k6_sotto_voice_noise_preview.py',
])
def test_standalone_script_help_from_unrelated_directory(tmp_path,script):
    proc=subprocess.run([sys.executable,str(ROOT/'tools'/script),'--help'],
                        cwd=tmp_path,capture_output=True,text=True,timeout=20)
    assert proc.returncode==0,proc.stderr
    assert '--help' in proc.stdout
