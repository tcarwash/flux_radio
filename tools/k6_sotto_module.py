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
SOTTO_LIVE_SCRATCH_REQUIRED=4080
SOTTO_ARENA_BYTES=14976
SOTTO_SCRATCH_RESERVE=1040
SOTTO_MODULE_OVERHEAD=24

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
  signed char cb[20], centroids[16*IVF_CD];
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
    # The sole fft256 caller zeroes im[0:256] before bit reversal, so the
    # imaginary load/store swap is dead. Keep the upstream source untouched.
    if text.count("    im[i] = 0;") != 1 or text.count("  fft256(re, im);") != 1:
        raise ValueError("Unexpected zero-imaginary FFT precondition")
    once("      t = im[i], im[i] = im[j], im[j] = t;",
         "      /* skip swapping two known-zero imaginary samples */")
    return text

def build(host_resources=False,live_tx=False,tone_tx=False,gp0_irq_tx=False):
    selected=sum(bool(x) for x in (live_tx,tone_tx,gp0_irq_tx))
    if selected>1:
        raise ValueError("Select exactly one Sotto TX input implementation")
    if selected and not host_resources:
        raise ValueError("Sotto TX requires typed host resources")
    import subprocess
    rev=subprocess.check_output(["git","-C",str(ROOT/"sotto"),"rev-parse","HEAD"],text=True).strip()
    if rev!=PIN:raise ValueError("Unexpected Sotto pin; requalify codec and SPI tables")
    with tempfile.TemporaryDirectory() as d:
        inc=Path(d)/"sotto_slim_context.inc"
        inc.write_text(adapt((FIXED/"sotto_fx_slim.c").read_text()))
        source=(SRC.with_name("sotto_gp0_irq_tx.c") if gp0_irq_tx else
                SRC.with_name("sotto_tone_tx.c" if tone_tx else "sotto_live_tx.c")) if selected else SRC
        code,entry=k6mod.build(str(source),cflags=[*k6mod.CFLAGS,"-I"+d,
            "-I"+str(FIXED),"-isystem",str(FIXED/"cm4/inc"),
            "-DSOTTO_V36","-DSOTTO_PITCH_THIRD","-DFX_NARROW_MUL",
            "-Wno-unused-function","-Wno-unused-const-variable","-Wno-unused-variable"] + (["-DSOTTO_HOST_RESOURCES"] if host_resources else []) + (["-DSOTTO_TONE_TX_ARMED=1"] if tone_tx else []))
    if entry!=k6mod.bin_entry(code):raise ValueError("Unexpected module entry")
    return code,entry

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out",required=True,type=Path)
    p.add_argument("--host-resources",action="store_true",help="read only typed K6CB/K6SC resource API")
    p.add_argument("--live-tx",action="store_true",help="EXPERIMENTAL live Sotto TX level30, no RF on install")
    p.add_argument("--tone-tx",action="store_true",help="OPT-IN 1 kHz Sotto PCM TX; only a deliberate RUN transmits")
    p.add_argument("--gp0-irq-tx",action="store_true",help="EXPERIMENTAL Sotto TX using physical GP0 DMA IRQ9 microphone capture")
    a=p.parse_args()
    code,entry=build(a.host_resources,a.live_tx,a.tone_tx,a.gp0_irq_tx)
    deck=build_deck(SLIM_CELLS.read_text())
    a.out.mkdir(parents=True,exist_ok=False)
    files={"module.bin":code,"sotto-ivf.k6sc":deck}
    for f,b in files.items():(a.out/f).write_bytes(b)
    selected=a.live_tx or a.tone_tx or a.gp0_irq_tx
    scratch_available=SOTTO_ARENA_BYTES-SOTTO_SCRATCH_RESERVE-len(code)-SOTTO_MODULE_OVERHEAD
    if selected and scratch_available<SOTTO_LIVE_SCRATCH_REQUIRED:
        raise ValueError(f"Sotto live TX needs {SOTTO_LIVE_SCRATCH_REQUIRED} scratch bytes; arena provides {scratch_available}")
    manifest={"format":1,"build":"Sotto GP0 IRQ microphone TX" if a.gp0_irq_tx else "Sotto experimental live TX" if a.live_tx else "Sotto 1 kHz generated PCM TX" if a.tone_tx else "Sotto qualification module (no RF)",
              "build_id":"0000000000000000",
              "scratch_required":SOTTO_LIVE_SCRATCH_REQUIRED if selected else 0,
              "arena_14976_scratch_upper":scratch_available,
              "scratch_margin":scratch_available-SOTTO_LIVE_SCRATCH_REQUIRED if selected else None,
              "library":[{"id":"sotto-gp0-irq" if a.gp0_irq_tx else "sotto-tx-live" if a.live_tx else "sotto-tone" if a.tone_tx else "sotto-test","kind":"module","name":"SOTTO GP0" if a.gp0_irq_tx else "SOTTO MIC" if a.live_tx else "SOTTO TONE" if a.tone_tx else "SOTTO TEST",
                          "file":"module.bin","level":30 if (a.live_tx or a.gp0_irq_tx) else 31 if a.tone_tx else 5}],
              "sha256":{f:hashlib.sha256(b).hexdigest() for f,b in files.items()}}
    (a.out/"manifest.json").write_text(json.dumps(manifest,indent=2)+"\n")
    print(json.dumps({"entry":entry,"module_bytes":len(code),"deck_bytes":len(deck),
                      "arena_11776_scratch_upper":11776-SOTTO_SCRATCH_RESERVE-len(code)-SOTTO_MODULE_OVERHEAD,
                      "arena_14976_scratch_upper":scratch_available,
                      "scratch_required":manifest["scratch_required"],
                      "scratch_margin":manifest["scratch_margin"],
                      "sha256":manifest["sha256"],"rf_enabled":selected},indent=2))

if __name__=="__main__":main()
