"""API26 read-only installed modem discovery (K6_MODE_REGISTRY=1 opt-in).

Limits (by design, not by omission): discovery reports which validated
installed library providers exist; it is NOT a packet payload transfer to an
arbitrary installed modem. Only the canonical SOTTO TEST voice module
(ID 0x0100, VOICE only, max_payload 0) enumerates, plus SOTTO RFTEST
(ID 0x0101) under the RFTEST opt-in. Native APRS ID 1 stays served by the
existing API13 TNC callback. No radio transmission is asserted or emitted.
"""
import re
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MOD_H = ROOT / "c/k6/k6_mod.h"
REG_H = ROOT / "c/k6/mod/k6_mode_registry.h"
OVERLAY = ROOT / "firmware/k6-aura/apply-mode-registry.py"
BUILD_SH = ROOT / "firmware/k6-aura/build-fd6818b.sh"
K6_BUILD = ROOT / "tools/k6_build.sh"


def compile_probe(extra=(), body=""):
    prog = "#include <stdio.h>\n#include \"k6_mod.h\"\n#include \"mod/k6_mode_registry.h\"\n" + body
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "probe.c"
        exe = Path(d) / "probe"
        src.write_text(prog)
        cmd = ["cc", "-O2", "-std=c99", "-Wall", "-Werror", "-I", str(ROOT / "c/k6"),
               *extra, str(src), "-o", str(exe)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        assert r.returncode == 0, "compile failed: " + r.stderr
        r = subprocess.run([str(exe)], capture_output=True, text=True)
        assert r.returncode == 0, "probe failed: " + r.stderr
        return r.stdout


def test_default_level_unchanged():
    out = compile_probe(body="int main(void){printf(\"%d\\n\",KM_LEVEL);return KM_LEVEL!=23;}\n")
    assert out.strip() == "23"


def test_struct_layout_stable():
    out = compile_probe(body="""
_Static_assert(sizeof(k6_mode_info_t)==18,"size");
_Static_assert(_Alignof(k6_mode_info_t)==2,"align");
_Static_assert(__builtin_offsetof(k6_mode_info_t,mode_id)==0,"id");
_Static_assert(__builtin_offsetof(k6_mode_info_t,flags)==2,"flags");
_Static_assert(__builtin_offsetof(k6_mode_info_t,name_len)==3,"nlen");
_Static_assert(__builtin_offsetof(k6_mode_info_t,name)==4,"name");
_Static_assert(__builtin_offsetof(k6_mode_info_t,max_payload)==16,"maxp");
int main(void){printf("ok\\n");return 0;}
""")
    assert out.strip() == "ok"


def test_opt_in_level_and_append_only():
    out = compile_probe(["-DK6_MODE_REGISTRY=1"], body="""
_Static_assert(KM_LEVEL==26,"level");
_Static_assert(__builtin_offsetof(k6_api_t,mode_info)>__builtin_offsetof(k6_api_t,cancel_poll),"append");
int main(void){printf("%d\\n",KM_LEVEL);return 0;}
""")
    assert out.strip() == "26"


def test_matcher_validates_identity():
    out = compile_probe(body="""
static int fails=0;
#define CHECK(c) do{if(!(c)){fails++;printf("FAIL %d\\n",__LINE__);}}while(0)
int main(void){
 char t[12]="SOTTO TEST", f[12]="SOTTO RFTEST", g[12]="SOTTO DEV", e[12]={0};
 char pfx[12]="SOTTO TESTX";
 CHECK(k6mode_match(1,5,26,t,0)==0x0100);
 CHECK(k6mode_match(1,26,26,t,0)==0x0100);
 CHECK(k6mode_match(1,5,26,f,0)==0);       /* rftest needs opt-in */
 CHECK(k6mode_match(1,5,26,f,1)==0x0101);
 CHECK(k6mode_match(1,5,26,g,1)==0);       /* no guessing from names */
 CHECK(k6mode_match(1,5,26,pfx,0)==0);     /* exact 12-byte match only */
 CHECK(k6mode_match(1,5,26,e,0)==0);
 CHECK(k6mode_match(2,5,26,t,0)==0);       /* page flag refused */
 CHECK(k6mode_match(4,5,26,t,0)==0);       /* deck flag refused */
 CHECK(k6mode_match(0,5,26,t,0)==0);
 CHECK(k6mode_match(1,0,26,t,0)==0);
 CHECK(k6mode_match(1,27,26,t,0)==0);      /* level above API refused */
 CHECK(k6mode_match(1,5,25,t,0)==0x0100);  /* level within API ok */
 printf(fails?"bad\\n":"ok\\n");return fails!=0;
}
""")
    assert out.strip() == "ok"


def test_overlay_and_build_hooks():
    src = OVERLAY.read_text()
    compile(src, str(OVERLAY), "exec")
    for needle in ["pub mode_info", "mod_mode_info", "k6_mode_registry",
                   "K6ModeInfo", "k6_mode_rftest", "SOTTO TEST", "SOTTO RFTEST",
                   "cancel_poll", "KL_SLOTS", "ffi::kl_info"]:
        assert needle in src, needle
    sh = BUILD_SH.read_text()
    assert "apply-mode-registry.py" in sh and "k6_mode_registry" in sh
    assert "K6_MODE_RFTEST" in sh
    kb = K6_BUILD.read_text()
    assert "-DK6_MODE_REGISTRY=1" in kb
    h = MOD_H.read_text()
    assert h.count("int (*mode_info)(uint32_t index, k6_mode_info_t *out);") == 1
    assert "#define KM_LEVEL 23" in h
