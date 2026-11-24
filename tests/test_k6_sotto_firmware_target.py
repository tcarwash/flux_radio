"""Sotto development build contract and offline linked-image gates.

Built firmware artifacts are optional in CI; in a developer worktree they
are verified against their own frozen ELF rather than another Cargo build.
Nothing here opens K6 serial, SDR, RF, SPI or PTT.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess

import pytest

ROOT=Path(__file__).resolve().parents[1]
TARGET=ROOT/"firmware/k6-aura/build-sotto.sh"
OUT=ROOT/"firmware/k6-aura/out-fd6818b-sotto"
BIN=OUT/"flux-fd6818b-sotto.bin"


def test_new_profile_frozen_and_regular_build_unmodified():
    text=TARGET.read_text()
    required={
        "K6_SOTTO_SERVICE":"1", "K6_CODEC":"1200",
        "K6_DV_WIRE":"1","K6_SOTTO_OA_FAST":"1",
        "K6_SOTTO_MATH_OPT":"-Oz",
        "K6_SOTTO_DEC_OPT":"-Oz",
        "K6_LIVE_SERIAL":"1","K6_REMOTE_UI":"1",
        "K6_LEAN_TXDOG":"1",
        "K6_FSK_RX_SERVICE":"0","K6_OTA_SERVICE":"0",
        "K6_SCAN_SERVICE":"0","K6_NATIVE_FM":"0",
        "K6_FONT_SUBSET":"1","K6_THEME":"flux",
        "K6_IMAGE_NAME":"flux-fd6818b-sotto",
    }
    for variable,value in required.items():
        assert f"{variable}={value}" in text
    assert "out-fd6818b-sotto" in text
    assert "k6_sotto_firmware_audit.py" in text
    assert "flux-fd6818b-sotto.elf" in text
    assert "build-fd6818b.sh" in text
    assert "exec_flash" not in text and "k6_flash.py" not in text
    build=(ROOT/"firmware/k6-aura/build-fd6818b.sh").read_text()
    assert 'K6_SOTTO_SERVICE:-0' in build


@pytest.mark.parametrize("env,reason",[
    ({"K6_CODEC":"2400"},"K6_CODEC=1200"),
    ({"K6_FSK_RX_SERVICE":"1"},"FSK RX"),
    ({"K6_OTA_SERVICE":"1"},"cable-only"),
    ({"K6_LEAN_TXDOG":"0"},"watchdog"),
])
def test_refuses_conflicting_ambient_config_before_any_build(env,reason):
    e=os.environ.copy()
    for k in ("K6_CODEC","K6_FSK_RX_SERVICE","K6_OTA_SERVICE","K6_LEAN_TXDOG"):
        e.pop(k,None)
    e.update(env)
    p=subprocess.run(["sh",str(TARGET)],cwd=ROOT,env=e,
                     capture_output=True,text=True,timeout=5)
    assert p.returncode==2
    assert reason in p.stderr
    assert "Building Flux" not in p.stdout


def test_math_and_wire_are_opt_in_to_sotto_profile_only():
    txt=(ROOT/"tools/k6_build.sh").read_text()
    assert 'K6_SOTTO_OA_FAST' in txt and 'sotto_fx_oa_fast.c' in txt
    assert '-Dfx_oa_block=fx_oa_block_compact_unused' in txt
    assert 'K6_DV_WIRE' in txt and 'sotto_dv_wire.c' in txt
    assert 'K6_CODEC:-' in txt or 'K6_CODEC' in txt
    assert 'K6_CB:-' in txt or 'K6_CB' in txt
    assert 'K6_SOTTO_DEC_OPT' in txt and 'K6_SOTTO_MATH_OPT' in txt


@pytest.mark.skipif(not BIN.exists(),reason="No locally compiled Sotto dev image")
def test_real_built_sotto_elf_has_decoder_abi_and_safe_flash_size():
    result=subprocess.run(["python3",str(ROOT/"tools/k6_sotto_firmware_audit.py")],
                          cwd=ROOT,check=True,text=True,capture_output=True,timeout=25)
    a=json.loads(result.stdout)
    assert a["target"]=="Flux B-chip Sotto-1200 development firmware"
    assert a["image_bytes"]<=122880 and a["flash_headroom_bytes"]>=0
    assert a["linked_sotto_decoder_symbols"]
    assert a["guarded_wire_archive_symbols"]
    assert a["build_only"] and a["hardware_flash_approval"] is False


@pytest.mark.skipif(not BIN.exists(),reason="No locally compiled Sotto dev image")
def test_audit_fails_closed_on_invalid_binary(tmp_path):
    import shutil
    p=tmp_path
    p.joinpath("c-build").mkdir()
    for source,target in (
        (OUT/"flux-fd6818b-sotto.bin",p/"flux-fd6818b-sotto.bin"),
        (OUT/"flux-fd6818b-sotto.bin.sha256",p/"flux-fd6818b-sotto.bin.sha256"),
        (OUT/"size.txt",p/"size.txt"),
        (OUT/"flux-fd6818b-sotto.elf",p/"flux-fd6818b-sotto.elf"),
        (OUT/"c-build/libsotto_k6.a",p/"c-build/libsotto_k6.a"),
    ):
        shutil.copyfile(source,target)
    file=p/"flux-fd6818b-sotto.bin"
    with file.open("r+b") as handle:
        handle.seek(100)
        old=handle.read(1)
        handle.seek(100)
        handle.write(bytes([old[0]^1]))
    audit=subprocess.run(["python3",str(ROOT/"tools/k6_sotto_firmware_audit.py"),
                          "--out",str(p)],cwd=ROOT,capture_output=True,text=True)
    assert audit.returncode!=0 and "SHA file" in audit.stderr
