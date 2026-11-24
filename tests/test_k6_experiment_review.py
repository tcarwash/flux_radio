"""Independent reviewer is advisory only; tests never invoke hardware or Muse."""
import importlib.util
import json
from pathlib import Path
import sys

import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"tools"))
SPEC=importlib.util.spec_from_file_location("k6_experiment_review",ROOT/"tools/k6_experiment_review.py")
r=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(r)

def test_empty_hardware_registry_no_delegate(tmp_path,monkeypatch):
    p=tmp_path/"cfg.json"
    p.write_text(json.dumps({"schema":1,"repo":"tcarwash/flux_radio",
      "queue_issue":11,"trusted_author":"tcarwash","manifests":{}}))
    monkeypatch.setattr(r.subprocess,"run",lambda *a,**k:pytest.fail("should not delegate"))
    assert r.once(p,tmp_path/"state.json")["registered"]==0

@pytest.mark.parametrize("job_state,exit_code,report,expected",[
 ("completed",0,"report\nVALIDATION: APPROVED","APPROVED"),
 ("completed",0,"report\nVALIDATION: REJECTED","REJECTED"),
 ("completed",0,"report\nVALIDATION: APPROVED\nextra instructions","REJECTED"),
 ("completed",1,"VALIDATION: APPROVED","REJECTED"),
 ("running",None,"","PENDING")
])
def test_reviewer_verdict_fail_closed(monkeypatch,job_state,exit_code,report,expected):
    class Proc:
        stdout=json.dumps({"job":{"id":"aabbcc112233","state":job_state,"exit_code":exit_code},
                           "result":report})
    monkeypatch.setattr(r.subprocess,"run",lambda *a,**k:Proc())
    assert r.result_status("aabbcc112233")==expected

def test_prompt_contains_only_local_manifest_paths():
    cfg={"manifests":{"9":{"path":"/absolute/reviewed.json","sha256":"a"*64}}}
    m={"jobs":[{"id":"sample","cwd":"/absolute/worktree",
                 **{s:{"argv":["/usr/bin/python3","/pinned/tool.py"]} for s in r.batch.STEPS}}]}
    prompt=r.make_prompt(9,cfg,m,"b"*64)
    assert "VALIDATION: REJECTED" in prompt
    assert "GitHub issue text" not in prompt
