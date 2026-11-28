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
import types
_tools_pkg=sys.modules.get("tools")
if _tools_pkg is None or str(ROOT/"tools") not in getattr(_tools_pkg,"__path__",[]):
    _tools_pkg=types.ModuleType("tools")
    _tools_pkg.__path__=[str(ROOT/"tools")]
    sys.modules["tools"]=_tools_pkg
from modem import k6mod
from tools.k6_sotto_ivf_deck import build_deck,SLIM_CELLS

FIXED=ROOT/"c/fixed"
SRC=ROOT/"c/k6/mod/sotto_tx_selftest.c"
PIN="598f1bce87c22ebada2b9d0d1fcfd89107b33a09"
SOTTO_LIVE_SCRATCH_REQUIRED=4080
SOTTO_MICRO_SCRATCH_SAVED=196  # RAM micro saved from pinned Sotto on ARMv6-M
SOTTO_ARENA_BYTES=14976
SOTTO_SCRATCH_RESERVE=1040
SOTTO_MODULE_OVERHEAD=24

def adapt(text, *, ram_micro=False, fast_history_copy=False, fast_cb_pair=False, fast_lag_pair=False):
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
    if ram_micro:
        # Proven memory-only optimization from research/k6-codec-micro-20261126:
        # a[] and q[] do not overlap their FFT/search lifetimes. Constrain the
        # 4kHz history to the safe lower bound; no alteration to Sotto 1200 wire
        # data or the pinned upstream submodule.
        once("#define NX4 240                    /* 4 kHz history */",
             "#define NX4 222                    /* tight history; minimum 221 */")
        once("  int16_t a[MAXH];",
             "  /* a[] reuses FFT-dead low bins or spare lag-prefix workspace. */")
        once("  int16_t *re = e->w, *im = e->w + NW, *a = e->a, rk[K];",
             "  int16_t *re = e->w, *im = e->w + NW, *a = e->w, rk[K];")
        once("  int16_t *q = e->a;",
             "  int16_t *q = e->w + 2 * (S4 + 1);\n"
             "  _Static_assert(2 * (S4 + 1) + NL <= 2 * NW, \"lag workspace\");")
    if fast_history_copy:
        # These two rolling histories are 4-byte aligned and shift forward
        # by an even number of int16_t samples. A word loop is overlap-safe
        # here because destination is always below source; keep the generic
        # ABI memmove untouched for every other caller.
        helper = """typedef unsigned int slim_alias_u32 __attribute__((__may_alias__));
_Static_assert(sizeof(slim_alias_u32) == 4, "32-bit history copy word");
_Static_assert((__builtin_offsetof(struct slim_enc, x8) & 3u) == 0, "x8 alignment");
_Static_assert((__builtin_offsetof(struct slim_enc, x4) & 3u) == 0, "x4 alignment");
_Static_assert(((NX8 - HOP) & 1u) == 0 && ((NX4 - HOP / 2) & 1u) == 0, "history word counts");
static void slim_history_shift(int16_t *dst, const int16_t *src, unsigned samples) {
  slim_alias_u32 *d = (slim_alias_u32 *)(void *)dst;
  const slim_alias_u32 *s = (const slim_alias_u32 *)(const void *)src;
  for (samples >>= 1; samples; samples--) *d++ = *s++;
}
"""
        once("unsigned slim_enc_bytes(void)", helper + "unsigned slim_enc_bytes(void)")
        once("  memmove(e->x8, e->x8 + HOP, (NX8 - HOP) * sizeof(int16_t));",
             "  slim_history_shift(e->x8, e->x8 + HOP, NX8 - HOP);")
        once("  memmove(e->x4, e->x4 + HOP / 2, (NX4 - HOP / 2) * sizeof(int16_t));",
             "  slim_history_shift(e->x4, e->x4 + HOP / 2, NX4 - HOP / 2);")
    if fast_cb_pair:
        # cb_hn is called only with the even fixed widths IVF_CD (8) and
        # CBB (20). Pair two adjacent dimensions per loop while preserving
        # the original accumulation order and exact integer arithmetic.
        old = """static int32_t cb_hn(const signed char *y, const int16_t *t, int n) {
  int32_t hsum = 0;
  int k;
  for (k = 0; k < n; k++) hsum += y[k] * (32 * y[k] - t[k]);
  return hsum;
}"""
        new = """_Static_assert((IVF_CD & 1) == 0 && (CBB & 1) == 0, "paired codebook widths");
static int32_t cb_hn(const signed char *y, const int16_t *t, int n) {
  int32_t hsum = 0;
  int k;
  for (k = 0; k < n; k += 2) {
    int y0 = y[k], y1 = y[k + 1];
    hsum += y0 * (32 * y0 - t[k]);
    hsum += y1 * (32 * y1 - t[k + 1]);
  }
  return hsum;
}"""
        once(old,new)
    if fast_lag_pair:
        # Source is the pinned fixed profile: W4=120 and SUB2=2.
        # Preserve each product's independent shift/round and the original
        # serial accumulation order; only eliminate half the loop branches.
        once('  for (L = LMIN - 1; L <= LMAX + 1; L++) {',
             '  _Static_assert(SUB2 == 2 && W4 % (2 * SUB2) == 0, "paired lag stride");\n'
             '  for (L = LMIN - 1; L <= LMAX + 1; L++) {')
        once('    for (i = 0; i < W4; i += SUB2) r += ((int32_t)p0[i] * p1[i]) >> sh;',
             '    for (i = 0; i < W4; i += 2 * SUB2) {\n'
             '      r += ((int32_t)p0[i] * p1[i]) >> sh;\n'
             '      r += ((int32_t)p0[i + SUB2] * p1[i + SUB2]) >> sh;\n'
             '    }')
    return text

def build(host_resources=False,live_tx=False,tone_tx=False,gp0_irq_tx=False,*,gp0_wiener_fir_tx=False,synthetic_tx=False,synth_profile=1,synth_hz=443500000,ram_micro=False,fast_div=False,fast_history_copy=False,fast_cb_pair=False,fast_lag_pair=False):
    if isinstance(synth_profile,bool) or synth_profile not in range(4):
        raise ValueError("Synthetic PCM profile must be 0..3")
    if synth_hz not in (433500000,443500000):
        raise ValueError("Synthetic test carrier must be the qualified 70cm bench frequency")
    selected=sum(bool(x) for x in (live_tx,tone_tx,gp0_irq_tx,gp0_wiener_fir_tx,synthetic_tx))
    if selected>1:
        raise ValueError("Select exactly one Sotto TX input implementation")
    if selected and not host_resources:
        raise ValueError("Sotto TX requires typed host resources")
    import subprocess
    rev=subprocess.check_output(["git","-C",str(ROOT/"sotto"),"rev-parse","HEAD"],text=True).strip()
    if rev!=PIN:raise ValueError("Unexpected Sotto pin; requalify codec and SPI tables")
    with tempfile.TemporaryDirectory() as d:
        inc=Path(d)/"sotto_slim_context.inc"
        inc.write_text(adapt((FIXED/"sotto_fx_slim.c").read_text(),ram_micro=ram_micro,fast_history_copy=fast_history_copy,fast_cb_pair=fast_cb_pair,fast_lag_pair=fast_lag_pair))
        source=(SRC.with_name("sotto_gp0_irq_tx.c") if (gp0_irq_tx or gp0_wiener_fir_tx) else
                SRC.with_name("sotto_synth_tx.c") if synthetic_tx else
                SRC.with_name("sotto_tone_tx.c" if tone_tx else "sotto_live_tx.c")) if selected else SRC
        code,entry=k6mod.build(str(source),cflags=[*k6mod.CFLAGS,"-I"+d,
            "-I"+str(FIXED),"-isystem",str(FIXED/"cm4/inc"),
            "-DSOTTO_V36","-DSOTTO_PITCH_THIRD","-DFX_NARROW_MUL",
            "-Wno-unused-function","-Wno-unused-const-variable","-Wno-unused-variable"] + (["-DSOTTO_HOST_RESOURCES"] if host_resources else []) + (["-DSOTTO_TONE_TX_ARMED=1"] if tone_tx else []) + (["-DSOTTO_GP0_WIENER_FIR=1"] if gp0_wiener_fir_tx else []) + (["-DSOTTO_SYNTH_TX_ARMED=1",f"-DSOTTO_SYNTH_PROFILE={synth_profile}",f"-DSYNTH_HZ={synth_hz}u"] if synthetic_tx else []) + (["-DSOTTO_FAST_DIV=1"] if fast_div else []))
    scratch_required=SOTTO_LIVE_SCRATCH_REQUIRED - (SOTTO_MICRO_SCRATCH_SAVED if ram_micro else 0)
    if selected and (fast_cb_pair or fast_lag_pair or ram_micro):
        scratch_available=SOTTO_ARENA_BYTES-SOTTO_SCRATCH_RESERVE-len(code)-SOTTO_MODULE_OVERHEAD
        scratch_margin=scratch_available-scratch_required
        if scratch_margin<48:
            raise ValueError(f"experimental encoder loop optimizations leave only {scratch_margin} scratch bytes; at least 48 are required")
    if entry!=k6mod.bin_entry(code):raise ValueError("Unexpected module entry")
    return code,entry

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out",required=True,type=Path)
    p.add_argument("--host-resources",action="store_true",help="read only typed K6CB/K6SC resource API")
    p.add_argument("--live-tx",action="store_true",help="EXPERIMENTAL live Sotto TX level30, no RF on install")
    p.add_argument("--tone-tx",action="store_true",help="OPT-IN 1 kHz Sotto PCM TX; only a deliberate RUN transmits")
    p.add_argument("--gp0-irq-tx",action="store_true",help="EXPERIMENTAL Sotto TX using physical GP0 DMA IRQ9 microphone capture")
    p.add_argument("--gp0-wiener-fir-tx",action="store_true",help="EXPERIMENTAL GP0 TX with offline-designed 15-tap Q15 Wiener FIR comparison filter")
    p.add_argument("--synthetic-tx",action="store_true",help="EXPERIMENTAL timed mock 8-kHz PCM into real Sotto codec and FSK TX; no microphone")
    p.add_argument("--synth-profile",type=int,choices=range(4),default=1,help="mock PCM: 0 silence, 1 multitone, 2 chirp, 3 bursts")
    p.add_argument("--synth-hz",type=int,choices=(433500000,443500000),default=443500000,help="strict 70cm dummy-load bench frequency")
    p.add_argument("--ram-micro",action="store_true",help="EXPERIMENTAL exact 196B Sotto state reduction via phase-exclusive scratch reuse")
    p.add_argument("--fast-div",action="store_true",help="EXPERIMENTAL shorter normalized ARMv6-M software divide; default off")
    p.add_argument("--fast-history-copy",action="store_true",help="EXPERIMENTAL aligned word copies for the two rolling PCM histories; default off")
    p.add_argument("--fast-cb-pair",action="store_true",help="EXPERIMENTAL paired codebook-score loop; default off")
    p.add_argument("--fast-lag-pair",action="store_true",help="EXPERIMENTAL paired exact lag-correlation loop; default off")
    a=p.parse_args()
    code,entry=build(a.host_resources,a.live_tx,a.tone_tx,a.gp0_irq_tx,gp0_wiener_fir_tx=a.gp0_wiener_fir_tx,synthetic_tx=a.synthetic_tx,synth_profile=a.synth_profile,synth_hz=a.synth_hz,ram_micro=a.ram_micro,fast_div=a.fast_div,fast_history_copy=a.fast_history_copy,fast_cb_pair=a.fast_cb_pair,fast_lag_pair=a.fast_lag_pair)
    deck=build_deck(SLIM_CELLS.read_text())
    a.out.mkdir(parents=True,exist_ok=False)
    files={"module.bin":code,"sotto-ivf.k6sc":deck}
    for f,b in files.items():(a.out/f).write_bytes(b)
    selected=a.live_tx or a.tone_tx or a.gp0_irq_tx or a.gp0_wiener_fir_tx or a.synthetic_tx
    scratch_available=SOTTO_ARENA_BYTES-SOTTO_SCRATCH_RESERVE-len(code)-SOTTO_MODULE_OVERHEAD
    scratch_required=SOTTO_LIVE_SCRATCH_REQUIRED-(SOTTO_MICRO_SCRATCH_SAVED if a.ram_micro else 0)
    if selected and scratch_available<scratch_required:
        raise ValueError(f"Sotto live TX needs {scratch_required} scratch bytes; arena provides {scratch_available}")
    manifest={"format":1,"build":"Sotto GP0 IRQ microphone TX with 15-tap Wiener FIR" if a.gp0_wiener_fir_tx else "Sotto GP0 IRQ microphone TX" if a.gp0_irq_tx else "Sotto autonomous mock PCM TX" if a.synthetic_tx else "Sotto experimental live TX" if a.live_tx else "Sotto 1 kHz generated PCM TX" if a.tone_tx else "Sotto qualification module (no RF)",
              "ram_micro_experimental":a.ram_micro,
              "fast_div_experimental":a.fast_div,
              "fast_history_copy_experimental":a.fast_history_copy,
              "fast_cb_pair_experimental":a.fast_cb_pair,
              "fast_lag_pair_experimental":a.fast_lag_pair,
              "synthetic_profile":a.synth_profile if a.synthetic_tx else None,
              "synthetic_hz":a.synth_hz if a.synthetic_tx else None,
              "build_id":"0000000000000000",
              "scratch_required":scratch_required if selected else 0,
              "arena_14976_scratch_upper":scratch_available,
              "scratch_margin":scratch_available-scratch_required if selected else None,
              "library":[{"id":"sotto-gp0-wiener-fir15" if a.gp0_wiener_fir_tx else "sotto-gp0-irq" if a.gp0_irq_tx else "sotto-synthetic" if a.synthetic_tx else "sotto-tx-live" if a.live_tx else "sotto-tone" if a.tone_tx else "sotto-test","kind":"module","name":"SOTTO FIR" if a.gp0_wiener_fir_tx else "SOTTO GP0" if a.gp0_irq_tx else "SOTTO SYNTH" if a.synthetic_tx else "SOTTO MIC" if a.live_tx else "SOTTO TONE" if a.tone_tx else "SOTTO TEST",
                          "file":"module.bin","level":29 if a.synthetic_tx else 30 if (a.live_tx or a.gp0_irq_tx or a.gp0_wiener_fir_tx) else 31 if a.tone_tx else 5}],
              "sha256":{f:hashlib.sha256(b).hexdigest() for f,b in files.items()}}
    (a.out/"manifest.json").write_text(json.dumps(manifest,indent=2)+"\n")
    print(json.dumps({"entry":entry,"module_bytes":len(code),"deck_bytes":len(deck),
                      "arena_11776_scratch_upper":11776-SOTTO_SCRATCH_RESERVE-len(code)-SOTTO_MODULE_OVERHEAD,
                      "arena_14976_scratch_upper":scratch_available,
                      "scratch_required":manifest["scratch_required"],
                      "scratch_margin":manifest["scratch_margin"],
                      "sha256":manifest["sha256"],"rf_enabled":selected},indent=2))

if __name__=="__main__":main()
