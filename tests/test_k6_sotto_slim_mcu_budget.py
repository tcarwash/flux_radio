"""Read-only cross-compiled slim encoder memory budget regression."""
from pathlib import Path
import pytest
from tools.k6_sotto_slim_mcu_budget import (ARENA_BYTES,CODEBOOK_BYTES,
                                              MODEL_STACK_BYTES,MODEL_STATE_BYTES,
                                              read_elf_sections,budget)
ELF=Path('/home/tyler/flux-bench/micvoice-20261123/sotto-slim-cm0-build-20261123/slim.elf')


def test_budget_accounts_for_known_codebook_and_rejects_bad_subtraction():
    assert MODEL_STACK_BYTES==676 and MODEL_STATE_BYTES==3148
    assert CODEBOOK_BYTES==30720 and ARENA_BYTES==15408
    with pytest.raises(ValueError):
        budget(ELF,codebook_externalized=CODEBOOK_BYTES+1) if ELF.exists() else _invalid_without_elf()


def _invalid_without_elf():
    raise ValueError('ELF not on this host')


def test_compiled_slim_arm_static_memory_model_and_audio_buffers():
    if not ELF.is_file():pytest.skip('Cortex-M0 compiled ELF not present outside research bench')
    x=budget(ELF)
    assert x['proof_level']=='STATIC_MODEL_ONLY'
    assert x['elf_sections_bytes']['.text']>5000
    assert x['elf_sections_bytes']['.rodata']>CODEBOOK_BYTES
    assert x['resident_code_and_tables_bytes']==10490
    assert x['room_before_buffers_and_runtime']==1094
    single=[z for z in x['scenarios'] if z['audio_40ms_buffers']==1]
    double=[z for z in x['scenarios'] if z['audio_40ms_buffers']==2]
    assert all(q['nominal_fits'] for q in single)
    assert all(not q['nominal_fits'] for q in double)
    assert next(z for z in x['scenarios'] if z['audio_40ms_buffers']==2 and z['l64_packets_queued']==2)['remaining_before_unknown_runtime_and_alignment']==-314
    assert len(x['artifact_sha256'])==64
