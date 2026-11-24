#!/usr/bin/env python3
"""Build the actual relocatable non-radiating Sotto module qualification binary.

The pinned upstream slim encoder has macros for SPI table lookup, but no
context argument. Adapt only the shape-search call chain, in a temporary
file, so the encoder owns its flash context. Never modify the submodule.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from modem import k6mod
from tools.k6_sotto_ivf_deck import build_deck,SLIM_CELLS

FIXED=ROOT/"c/fixed"
SRC=ROOT/"c/k6/mod/sotto_tx_selftest.c"
PIN="598f1bce87c22ebada2b9d0d1fcfd89107b33a09"

def adapt(text):
    def once(old,new):
        nonlocal text
        if text.count(old)!=1:raise ValueError("Unexpected upstream context: "+old)
        text=text.replace(old,new)
    once("struct slim_enc {","struct slim_enc {\n  sotto_io_t *io;")
    for name,typ,expected in (("cb_search","int",4),("cb_sub","int32_t",4),
                              ("code_a","int",2),("code_b","int32_t",3)):
        pattern=rf"\b{name}\("
        hits=len(re.findall(pattern,text))
        if hits!=expected:raise ValueError(f"Unexpected upstream {name}: {hits}")
        text=re.sub(pattern,name+"(io, ",text)
        once(f"static {typ} {name}(io, ",f"static {typ} {name}(sotto_io_t *io, ")
    for name in ("slim_coder_push","slim_coder_flush"):
        pattern=rf"(int {name}\([^\n]+\) \{{)"
        text,n=re.subn(pattern,r"\1\n  sotto_io_t *io=e->io;",text)
        if n!=1:raise ValueError(f"Unexpected upstream {name} entry")
    return text

def build():
    import subprocess
    rev=subprocess.check_output(["git","-C",str(ROOT/"sotto"),"rev-parse","HEAD"],text=True).strip()
    if rev!=PIN:raise ValueError("Unexpected Sotto pin; requalify codec and SPI tables")
    with tempfile.TemporaryDirectory() as d:
        inc=Path(d)/"sotto_slim_context.inc"
        inc.write_text(adapt((FIXED/"sotto_fx_slim.c").read_text()))
        code,entry=k6mod.build(str(SRC),cflags=[*k6mod.CFLAGS,"-I"+d,
            "-I"+str(FIXED),"-isystem",str(FIXED/"cm4/inc"),
            "-DSOTTO_V36","-DSOTTO_PITCH_THIRD","-DFX_NARROW_MUL",
            "-Wno-unused-function","-Wno-unused-const-variable","-Wno-unused-variable"])
    if entry!=k6mod.bin_entry(code):raise ValueError("Unexpected module entry")
    return code,entry

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out",required=True,type=Path)
    a=p.parse_args()
    code,entry=build()
    deck=build_deck(SLIM_CELLS.read_text())
    a.out.mkdir(parents=True,exist_ok=False)
    files={"module.bin":code,"sotto-ivf.k6sc":deck}
    for f,b in files.items():(a.out/f).write_bytes(b)
    manifest={"format":1,"build":"Sotto qualification module (no RF)",
              "build_id":"0000000000000000",
              "library":[{"id":"sotto-test","kind":"module","name":"SOTTO TEST",
                          "file":"module.bin","level":5}],
              "sha256":{f:hashlib.sha256(b).hexdigest() for f,b in files.items()}}
    (a.out/"manifest.json").write_text(json.dumps(manifest,indent=2)+"\n")
    print(json.dumps({"entry":entry,"module_bytes":len(code),"deck_bytes":len(deck),
                      "arena_11776_scratch_upper":11776-len(code)-24,
                      "arena_15408_scratch_upper":15408-len(code)-24,
                      "sha256":manifest["sha256"],"rf_enabled":False},indent=2))

if __name__=="__main__":main()
