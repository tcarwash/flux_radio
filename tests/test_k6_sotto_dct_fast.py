"""Exhaustive native C check of the compact DCT's actual 20x20 domain."""
import ctypes
from pathlib import Path
import subprocess
import shutil
import pytest

ROOT=Path(__file__).resolve().parents[1]

def test_dct_reciprocal_exact(tmp_path):
    if not shutil.which("cc"):pytest.skip("native compiler unavailable")
    src=tmp_path/"dct.c";so=tmp_path/"dct.so"
    src.write_text('#include "dv_live_slim_dct.h"\n'
                   'int dct(int j,int i){return dv_live_dct_at(j,i);}\n'
                   '#include "fx_tables.c"\n'
                   'int reference(int j,int i){return fx_dct[j*20+i];}\n')
    subprocess.run(["cc","-O2","-shared","-fPIC","-DSOTTO_V36","-DSOTTO_PITCH_THIRD",
                    "-DFX_NARROW_MUL","-I"+str(ROOT/"c/k6/mod"),"-I"+str(ROOT/"c/fixed"),
                    str(src),"-o",str(so)],check=True)
    lib=ctypes.CDLL(str(so))
    # The encoder skips the differently normalized DC row (j=0).
    assert all(m-((m*205)>>14)*80==m%80 for m in range(742))
    for j in range(1,20):
        for i in range(20):
            assert lib.dct(j,i)==lib.reference(j,i),(j,i)
