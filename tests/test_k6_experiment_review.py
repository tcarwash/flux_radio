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
 ("completed",1,"VALIDATION: APPROVED","INFRA_INTERRUPTED"),
 ("running",None,"","PENDING"),
 ("interrupted",None,"","INFRA_INTERRUPTED"),
 ("timed_out",143,"","INFRA_INTERRUPTED")
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


def test_interrupted_read_only_review_requeues_once_without_hardware(monkeypatch,tmp_path):
    n=9
    cfg={"repo":"tcarwash/flux_radio","queue_issue":11,"trusted_author":"tcarwash",
         "manifests":{"9":{"path":"/local/test.json","sha256":"a"*64}}}
    api_raw=b"api29"
    manifest={"jobs":[{"id":"x","cwd":"/local/worktree",**{
        s:{"argv":["/usr/bin/python3","/local/a.py"],"timeout_seconds":5}
        for s in r.batch.STEPS}}]}
    key=r.fingerprint(n,"a"*64,api_raw)
    state_path=tmp_path/"review.json"
    r.journal.state_write(state_path,{"schema":1,"jobs":{key:{
        "state":"REVIEW_SUBMITTED","job_id":"abcd1234abcd","attempts":1
    }}})
    prompt_root=tmp_path/"prompts"
    prompt_root.mkdir()
    prompt=prompt_root/f"hardware-{key}.txt"
    prompt.write_text(r.make_prompt(n,cfg,manifest,r.hw.digest_for(b"manifest",api_raw)))
    monkeypatch.setattr(r.hw,"load_config",lambda *a:cfg)
    monkeypatch.setattr(r.batch,"read_api_config",lambda *a:({"target_module_api":29},api_raw))
    monkeypatch.setattr(r.hw,"load_registered_manifest",lambda *a:(manifest,"a"*64,b"manifest"))
    monkeypatch.setattr(r,"result_status",lambda _: "INFRA_INTERRUPTED")
    jobs=[]
    monkeypatch.setattr(r,"delegate_readonly",lambda p:jobs.append(p) or "beef0000beef")
    out=r.once(tmp_path/"fake.json",state_path,prompt_root)
    assert out["events"][0]["action"]=="REVIEW_INFRA_REQUEUED"
    assert out["events"][0]["attempt"]==2
    assert jobs==[prompt]
    after=r.journal.state_load(state_path)["jobs"][key]
    assert after["job_id"]=="beef0000beef"
    assert after["attempts"]==2
    assert after["state"]=="REVIEW_SUBMITTED"
