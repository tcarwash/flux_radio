"""Hardware telemetry is evidence, not a free-form fitted simulator parameter."""
import json
import pytest
from tools.k6_sotto_gp0_hardware_envelope import read_aa3,envelope,AA3_RECORD


def test_physical_regression_is_exact_and_well_scoped():
    record=read_aa3()
    assert record["measured_capture_ms"]==240.11
    assert record["peak_isr_ticks"]==3488
    assert record["resource_reads"]==1034
    assert record["pcm_lost"] and record["module_return"]==-12
    assert record["module_serial"]==1795751383


def test_reject_mismatched_bootlocal_telemetry(tmp_path):
    record=json.loads(AA3_RECORD.read_text())
    record["raw_readback"]["timing"]["module_serial"]+=1
    p=tmp_path/"mismatched.json";p.write_text(json.dumps(record))
    with pytest.raises(ValueError,match="provenance"):
        read_aa3(p)
    record=json.loads(AA3_RECORD.read_text())
    record["raw_readback"]["timing"]["gp0_code3_reason_id"]=1
    p.write_text(json.dumps(record))
    with pytest.raises(ValueError,match="PCM queue"):
        read_aa3(p)


def test_metadata_explicitly_marks_mean_irq_cost_unknown():
    r=envelope(execute=False)
    assert r["hardware"]["working_unfiltered"]["peak_isr_ticks"]==3147
    assert r["hardware"]["aa3_failure"]["peak_isr_ticks"]==3488
    assert r["irq_rate_hz"]==10907.5
    assert r["scenarios"]==[]
    assert "not cycle-accurate" in r["test_type"]
    assert any("mean" in msg for msg in r["limitations"])


def test_envelope_rejects_unbounded_peak_fractions():
    for ratio in (-.2,0,1.01,float("inf")):
        with pytest.raises(ValueError):
            envelope(execute=False,peak_fractions=[ratio])
