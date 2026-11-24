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
    # Reuse the analysis FFT/lag workspace only AFTER analysis returns.
    # A typed union keeps the path records and the nested search buffers
    # disjoint and avoids creating a second 500B stack working set.
    path_type="""typedef struct {
  int16_t sa[2];
  int16_t rec[NC];
  int32_t err;
} apath_t;"""
    once(path_type,"")
    once("struct slim_enc {",path_type+"""
typedef struct {
  apath_t paths[SLIM_MAXP];
  int16_t target[20], vectors[IVF_MAXN];
  signed char cb[20], centroids[32*IVF_CD];
  int32_t best_dist[SLIM_MAXP], cell_dist[IVF_MAXP];
  unsigned char cell_id[IVF_MAXP];
} sotto_coder_work_t;
_Static_assert(sizeof(sotto_coder_work_t)<=2*NW*sizeof(int16_t),"workspace too large");
struct slim_enc {
  sotto_io_t *io;""")
    once("  int16_t w[2 * NW];",
         "  union { int16_t w[2 * NW]; sotto_coder_work_t coder; };")
    for name,typ,expected in (("cb_search","int",4),("cb_sub","int32_t",4),
                              ("code_a","int",2),("code_b","int32_t",3)):
        pattern=rf"\b{name}\("
        hits=len(re.findall(pattern,text))
        if hits!=expected:raise ValueError(f"Unexpected upstream {name}: {hits}")
        text=re.sub(pattern,name+"(e, ",text)
        once(f"static {typ} {name}(e, ",f"static {typ} {name}(slim_enc_t *e, ")
        if name in ("cb_search","cb_sub"):
            pattern=rf"(static {typ} {name}\([^\n]+\) \{{)"
            text,n=re.subn(pattern,r"\1\n  sotto_io_t *io=e->io;",text)
            if n!=1:raise ValueError(f"Unexpected upstream {name} entry")
    once("  apath_t p[SLIM_MAXP];","  apath_t *p=e->coder.paths;")
    once("  int16_t t[CBB];\n  signed char buf[CBB];\n  int32_t bh[SLIM_MAXP];",
         "  int16_t *t=e->coder.target;\n  signed char *buf=e->coder.cb;\n  int32_t *bh=e->coder.best_dist;")
    once("    int32_t ph[IVF_MAXP];\n    unsigned char pc[IVF_MAXP];\n    signed char cbuf[IVF_BLK * IVF_CD];\n    int16_t vl[IVF_MAXN];",
         "    int32_t *ph=e->coder.cell_dist;\n    unsigned char *pc=e->coder.cell_id;\n    signed char *cbuf=e->coder.centroids;\n    int16_t *vl=e->coder.vectors;")
    # `envelope` zeroes every im[i] before its only fft256 call. Swapping
    # two zeros during bit-reversal is unnecessary but costs ARMv6-M cycles.
    # Fail closed if upstream changes the FFT's exact precondition or body.
    if text.count("    im[i] = 0;") != 1 or text.count("  fft256(re, im);") != 1:
        raise ValueError("Unexpected zero-imaginary FFT precondition")
    once("      t = im[i], im[i] = im[j], im[j] = t;",
         "      /* imaginary input is all zeros: skip dead bit-reversal swap */")
    # Preserve the original centroid order while batching 32 at a time.
    # The scratch union is 1024 B, so the doubled 256 B centroid buffer
    # fits without increasing encoder arena or caller stack.
    once("#define IVF_BLK 16", "#define IVF_BLK 32")
    # The input block is consumed into x8 before lag_search/envelope ever
    # touches w. Reuse its first 80 samples for flush silence as well.
    # Keep the original upstream analysis and wire arithmetic unchanged.
    once("    int16_t z[HOP];\n    memset(z, 0, sizeof z);",
         "    int16_t *z = e->w;\n    memset(z, 0, HOP * sizeof(*z));")
    return text

def build(host_resources=False,live_test=False,live_tx=False):
    if live_tx and (not host_resources or live_test):raise ValueError("live TX requires API29 resources and separate build")
    if live_test and not host_resources:raise ValueError("live test requires API 27")
    import subprocess
    rev=subprocess.check_output(["git","-C",str(ROOT/"sotto"),"rev-parse","HEAD"],text=True).strip()
    if rev!=PIN:raise ValueError("Unexpected Sotto pin; requalify codec and SPI tables")
    with tempfile.TemporaryDirectory() as d:
        inc=Path(d)/"sotto_slim_context.inc"
        inc.write_text(adapt((FIXED/"sotto_fx_slim.c").read_text()))
        code,entry=k6mod.build(str(SRC.with_name("sotto_live_tx.c") if live_tx else SRC),cflags=[*k6mod.CFLAGS,"-I"+d,
            "-I"+str(FIXED),"-isystem",str(FIXED/"cm4/inc"),
            "-DSOTTO_V36","-DSOTTO_PITCH_THIRD","-DFX_NARROW_MUL",
            "-Wno-unused-function","-Wno-unused-const-variable","-Wno-unused-variable"] + (["-DSOTTO_HOST_RESOURCES"] if host_resources else []) + (["-DSOTTO_LIVE_TEST"] if live_test else []))
    if entry!=k6mod.bin_entry(code):raise ValueError("Unexpected module entry")
    return code,entry

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out",required=True,type=Path)
    p.add_argument("--host-resources",action="store_true",help="build API 27 balanced host module")
    p.add_argument("--live-test",action="store_true",help="bounded streaming qualification, no RF")
    p.add_argument("--live-tx",action="store_true",help="EXPERIMENTAL attended physical PTT TX module")
    a=p.parse_args()
    code,entry=build(a.host_resources,a.live_test,a.live_tx)
    deck=build_deck(SLIM_CELLS.read_text())
    a.out.mkdir(parents=True,exist_ok=False)
    files={"module.bin":code,"sotto-ivf.k6sc":deck}
    for f,b in files.items():(a.out/f).write_bytes(b)
    manifest={"format":1,"build":"Sotto experimental attended TX" if a.live_tx else "Sotto qualification module (no RF)",
              "build_id":"0000000000000000",
              "library":[{"id":"sotto-tx-bench" if a.live_tx else "sotto-live-test" if a.live_test else "sotto-test","kind":"module","name":"SOTTO TX" if a.live_tx else "SOTTO LIVE" if a.live_test else "SOTTO TEST",
                          "file":"module.bin","level":29 if a.live_tx else 27 if a.host_resources else 5}],
              "sha256":{f:hashlib.sha256(b).hexdigest() for f,b in files.items()}}
    (a.out/"manifest.json").write_text(json.dumps(manifest,indent=2)+"\n")
    print(json.dumps({"entry":entry,"module_bytes":len(code),"deck_bytes":len(deck),
                      "arena_11776_scratch_upper":11776-len(code)-24,
                      "arena_15408_scratch_upper":15408-len(code)-24,
                      "sha256":manifest["sha256"],"rf_enabled":a.live_tx},indent=2))

if __name__=="__main__":main()
