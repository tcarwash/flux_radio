"""TX-15X one-shot host bench hash and guard checks; no hardware access."""
from pathlib import Path
import hashlib
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"tools"))
from modem import k6mod
from k6_tx15x_rf_bench import EXPECTED_CODE_SHA256, run_transaction


def test_exact_rf_wrapper_build_hash_is_pinned(tmp_path):
    source=tmp_path/"tx15x-enabled.c"
    source.write_text(
        "#define K6_MODULE_LEVEL 3\n"
        "#define TX15X_BENCH_ENABLE 1\n"
        "#define TX15X_RF_ENABLE 1\n"
        "#define TX15X_FIXTURE_ACK 1\n"
        "#define TX15X_WATCHDOG_ACK 1\n"
        "#define TX15X_HZ 433500000u\n"
        f'#include "{ROOT / "c/k6/mod/tx15x_gain_dynamics.c"}"\n')
    code,entry=k6mod.build(str(source))
    assert entry==0
    assert hashlib.sha256(code).hexdigest()==EXPECTED_CODE_SHA256[1]


def test_host_transaction_requires_explicit_dummy_load(tmp_path):
    with pytest.raises(ValueError,match="dummy-load-confirmed"):
        run_transaction("/dev/null",tmp_path/"run",tmp_path/"source.c",
                        tmp_path/"backup.k6m",variant=1,armed=False)


def test_host_transaction_refuses_existing_output_and_backup(tmp_path):
    source=tmp_path/"source.c"
    source.write_text("unused")
    prefix=tmp_path/"run"
    prefix.with_suffix(".json").write_text("{}");
    with pytest.raises(FileExistsError,match="output IQ or metadata"):
        run_transaction("/dev/null",prefix,source,tmp_path/"backup.k6m",
                        variant=1,armed=True)
    prefix.with_suffix(".json").unlink()
    backup=tmp_path/"backup.k6m";backup.write_bytes(b"existing")
    with pytest.raises(FileExistsError,match="backup already exists"):
        run_transaction("/dev/null",prefix,source,backup,variant=1,armed=True)
