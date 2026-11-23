"""MANDATORY target-code ARMv6-M Unicorn integration gates, no skips.

These tests execute the real compiled Thumb firmware instruction stream,
including SPI-backed codebook/IVF reads through the fake flash memory map,
real slim_encode10 and exact guarded L64 packetizer. The model ELF is
explicitly NOT deployable or flashable.
"""
from __future__ import annotations
import json
from pathlib import Path
import subprocess

from tools.k6_live_dv_spi_m0_size_probe import build as build_arm

ROOT=Path(__file__).resolve().parents[1]
VENV=Path('/home/tyler/flux-bench/micvoice-20261123/sotto-slim-cm0-build-20261123/.venv-cm0/bin/python')
BITS=Path('/home/tyler/flux-bench/micvoice-20261123/sotto-slim-packet-run01/slim-sotto1200.bit')
PCM=Path('/home/tyler/flux-bench/micvoice-20261123/sotto-offline-run01/k6mic-resampled-8k.raw')


def run(script,*args):
    assert VENV.exists(),'Need the pinned offline Unicorn 2.1.4 environment (NO network install)'
    p=subprocess.run([str(VENV),str(ROOT/script),*map(str,args)],
                     cwd=ROOT,capture_output=True,text=True,timeout=110)
    assert p.returncode==0,p.stderr+'\n'+p.stdout
    return p


def test_actual_arm_queued_fifo_words_and_hard_fault_controls(tmp_path):
    assert BITS.exists(),'Need original private saved K6 Sotto fixture'
    linked=build_arm(tmp_path/'not-flashable')
    assert linked['linked_bss_bytes']==0
    assert linked['unsafe_to_deploy']
    elf=tmp_path/'not-flashable/NOT-FLASHABLE-live-dv-size-probe.elf'
    report=tmp_path/'actual-arm-guard-packets.json'
    run('tools/k6_live_dv_guard_unicorn.py','--elf',elf,'--bits',BITS,'--out',report)
    data=json.loads(report.read_text())
    assert data['emulated_actual_arm_code']
    assert data['n_frames']==26 and data['n_guard_packets']==4
    assert data['out_sha256']==(
        '923e1956c18ecfd75cdb5e8778ea6dbff93b55970a95f330054aa4cdf504f537')
    assert data['all_passed'] is True
    assert len(data['real_arm_unicorn_fault_cases'])>=5
    assert data['peak_packetizer_cycles']<25000


def test_combined_actual_ARM_slim_spi_guard_and_corrupt_deck_fails(tmp_path):
    assert PCM.exists(),'Need original real-microphone PCM fixture, no synthetic substitute'
    linked=build_arm(tmp_path/'unicorn-only',emulator_model=True)
    assert linked['linked_bss_bytes']==0
    assert linked['unsafe_to_deploy'] and not linked['flashable']
    elf=tmp_path/'unicorn-only/NOT-FLASHABLE-live-dv-size-probe.elf'
    report=tmp_path/'actual-arm-combined.json'
    run('tools/k6_live_dv_spi_unicorn.py','--elf',elf,'--pcm',PCM,'--out',report)
    data=json.loads(report.read_text())
    assert data['actual_arm_instructions_executed']
    assert data['codec_superframes']==26 and data['codec_10ms_calls']==104
    assert data['original_guard_frames']==4 and data['guard_bytes_total']==264
    assert data['guard_bytes_sha256']==(
        '923e1956c18ecfd75cdb5e8778ea6dbff93b55970a95f330054aa4cdf504f537')
    assert data['codec_sha256']==(
        '8c20829a11ce6c3f22162b6dcc15e0f4378bbdccfbdf967a6eb30b0bd4646ba5')
    assert data['k6sc_deck_corruption_refused'] is True
    assert data['modeled_stack_highwater_bytes']<=1024
    assert linked['optimistic_remaining_arena_bytes']>=2000
    assert data['unsafe_to_deploy'] and data['no_hardware_access']
