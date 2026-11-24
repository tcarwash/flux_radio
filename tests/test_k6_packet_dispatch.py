"""Dispatcher slice 1: LOOPBACK-EMU one-shot handoff (offline, no radio).

Validates without the Aura checkout or network:
- C identity gates (k6_mode_registry.h): exact LOOPBACK-EMU match only,
  voice/unknown/old-API refused, finite constants, -40 staged code.
- Overlay source gates: exact level-27/name/action-35/mode-0x0200 checks,
  single provider run (no retry loop/recursion), no RF or KISS-resume
  symbols in the dispatch path, real clock + finite TTL, fail-closed
  refusals, default (non-mailbox) lib_run path behavior-identical.
- Fixture chain: registry -> mailbox -> dispatch overlays apply
  idempotently on a synthetic pager.rs.
"""
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OVERLAY = ROOT / "firmware/k6-aura/apply-packet-dispatch.py"
BUILD_SH = ROOT / "firmware/k6-aura/build-fd6818b.sh"


def compile_probe(extra=(), body=""):
    prog = ("#include <stdio.h>\n#include <string.h>\n"
            "#include \"k6_mod.h\"\n"
            "#include \"mod/k6_mode_registry.h\"\n"
            "#include \"mod/k6_packet_mailbox.h\"\n" + body)
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "probe.c"
        exe = Path(d) / "probe"
        src.write_text(prog)
        cmd = ["cc", "-O2", "-std=c99", "-Wall", "-Werror",
               "-I", str(ROOT / "c/k6"), *extra, str(src), "-o", str(exe)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        assert r.returncode == 0, "compile failed: " + r.stderr
        r = subprocess.run([str(exe)], capture_output=True, text=True)
        assert r.returncode == 0, "probe failed: " + r.stderr
        return r.stdout


def test_match_packet_exact_only():
    out = compile_probe(["-DK6_MODE_REGISTRY=1", "-DK6_PACKET_MAILBOX=1"], body="""
static void set(char n[12], const char *s) { memset(n, 0, 12); memcpy(n, s, strlen(s)); }
int main(void) {
    char ok[12], lo[12], x[12], sotto[12], voice[12];
    set(ok, "LOOPBACK-EMU"); set(lo, "LOOPBACK");
    memcpy(x, "LOOPBACK-EMX", 12); set(sotto, "SOTTO TEST"); set(voice, "SOTTO RFTEST");
    if (k6mode_match_packet(KM_F_LIB, 27, 27, ok) != 0x0200) return 1;
    if (k6mode_match_packet(KM_F_LIB, 27, 27, lo) != 0) return 2;
    if (k6mode_match_packet(KM_F_LIB, 27, 27, x) != 0) return 3;
    if (k6mode_match_packet(KM_F_LIB, 27, 27, sotto) != 0) return 4;
    if (k6mode_match_packet(KM_F_LIB, 27, 27, voice) != 0) return 5;
    if (k6mode_match_packet(0, 27, 27, ok) != 0) return 6;          /* not a lib image */
    if (k6mode_match_packet(KM_F_LIB, 0, 27, ok) != 0) return 7;   /* level 0 */
    if (k6mode_match_packet(KM_F_LIB, 28, 28, ok) != 0x0200) return 8; /* in-API level */
    if (k6mode_match_packet(KM_F_LIB, 27, 26, ok) != 0) return 9;   /* host below 27 */
    /* API29 separation is enforced host-side (exact level == 27 gates in
       the dispatcher + provider), not in this matcher. */
    printf("ok\\n"); return 0;
}
""")
    assert out.strip() == "ok"


def test_provider_caps_voice_never_covers_tx():
    out = compile_probe(["-DK6_MODE_REGISTRY=1", "-DK6_PACKET_MAILBOX=1"], body="""
int main(void) {
    uint8_t f = 0; uint16_t m = 0;
    if (!k6mode_provider_caps(0x0200, &f, &m)) return 1;
    if (f != (K6_MODE_PACKET_TX | K6_MODE_PACKET_RX) || m != 128) return 2;
    if (!k6mode_can_tx(0x0200, 1) || !k6mode_can_tx(0x0200, 128)) return 3;
    if (k6mode_can_tx(0x0200, 0) || k6mode_can_tx(0x0200, 129)) return 4;
    if (!k6mode_provider_caps(0x0100, &f, &m)) return 5;
    if (f != K6_MODE_VOICE || m != 0) return 6;
    if (k6mode_can_tx(0x0100, 1) || k6mode_can_tx(0x0101, 1)) return 7;
    if (k6mode_provider_caps(0x0300, &f, &m) || k6mode_can_tx(0x0300, 1)) return 8;
    if (K6MB_TNC_STAGED != -40 || K6_ACTION_PACKET_PROVIDER != 35) return 9;
    if (KM_LEVEL != 27 || K6MB_VERSION != 1) return 10;
    printf("ok\\n"); return 0;
}
""")
    assert out.strip() == "ok"


def test_overlay_source_gates():
    src = OVERLAY.read_text()
    compile(src, str(OVERLAY), "exec")
    for needle in [
        'info.level == 27',
        'mb_name_is(&info.name, b"LOOPBACK-EMU")', "kl_load_action",
        "35,", "0x0200", "K6MB_TNC_STAGED", "K6MB_TTL_MS",
        "mb_host_clock_ms()", "wrapping_sub", "tnc_phase",
        "mb_begin_tnc_phase", "mb_end_tnc_phase",
        "mb_dispatch_loopback_provider", "run_module(app, syst); // provider phase",
        "TNC NOT auto-resumed", "UNSClosed".replace("Closed", "OLVED"),
        "LOOPBACK-EMU not installed", "provider auth failed",
        "no dispatch", "clear_store(app)",
    ]:
        assert needle in src, needle
    # Decode the Python triple-string and assert generated Rust emits the
    # four required NUL escape bytes, never a doubled-backslash spelling.
    import ast
    tree = ast.parse(src)
    core = next(n.value.value for n in tree.body
                if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "dispatch_block"
                        for t in n.targets))
    assert r'b"FLUX TNC\0\0\0\0"' in core
    assert r'b"FLUX TNC\\0\\0\\0\\0"' not in core
    # Exactly one provider execution, no retry/recursion/resume machinery.
    assert src.count("run_module(app, syst); // provider phase") == 1
    assert "while " not in src.split("fn mb_dispatch_loopback_provider")[1].split("fn run_module")[0].replace(
        "while i < 12", "")
    for banned in ["tx_on", "afsk_tx_send", "fsk_tx_send", "fd_write", "tune(",
                   "kiss_mode(1)", "km_again", "kl_load(", "LOOPBACK-EMU-X"]:
        assert banned not in src, banned
    # TTL is finite, never 0 or MAX; stage placeholders are gone here.
    assert "30_000" in src and "0xFFFFFFFF" in src  # only as an expiry guard
    # These legacy expressions appear only in the overlay's match
    # anchors; the generated Rust must replace them, validated below.
    assert 'bus.tx.created_ms = match mb_host_clock_ms()' in src
    assert 'bus.tx.ttl_ms = K6MB_TTL_MS;' in src
    # Default build unchanged: original KEPT line kept for the not(mailbox) arm.
    assert "not(all(k6_packet_mailbox" in src and "unsafe { KEPT = true };" in src
    sh = BUILD_SH.read_text()
    assert "apply-packet-dispatch.py" in sh
    assert sh.index("apply-packet-mailbox.py") < sh.index("apply-packet-dispatch.py")


FIXTURE_API = """pub struct K6Api {
    pub abi: u16, pub level: u16,
        pub cancel_poll: unsafe extern "C" fn() -> i32,
}
pub static MOD_API: K6Api = K6Api {
    abi: 1,
    level: if 26 { 26 } else { 23 },
    cancel_poll: mod_cancel_poll,
};
unsafe extern "C" fn mod_cancel_poll() -> i32 { -1 }
mod ffi {
    pub fn kl_info(slot: i32, fl: *const KlFlash, out: *mut KlInfo) -> i32;
}
fn run_module(app: &mut App, syst: &mut SYST) {
    let ret = 0;
}
fn lib_run(app: &mut App, syst: &mut SYST, mut n: u8) {
    if r > 0 {
        run_module(app, syst);
        unsafe { KEPT = true };
    }
}
"""


def run_overlay_chain(pager_rs: Path):
    for ov in ["apply-mode-registry.py", "apply-packet-mailbox.py",
               "apply-packet-dispatch.py"]:
        src = (ROOT / "firmware/k6-aura" / ov).read_text()
        code = src.replace(
            'Path(__file__).resolve().parent / "build/aura-fd6818b-port/src/app/pager.rs"',
            "Path(" + repr(str(pager_rs)) + ")")
        assert "build/aura-fd6818b-port" not in code, ov
        exec(compile(code, ov, "exec"), {})


def test_overlay_chain_idempotent_on_fixture():
    with tempfile.TemporaryDirectory() as d:
        pager_rs = Path(d) / "pager.rs"
        pager_rs.write_text(FIXTURE_API)
        run_overlay_chain(pager_rs)
        s = pager_rs.read_text()
        assert s.count("mb_dispatch_loopback_provider") == 2
        assert s.count("kl_load_action") >= 2
        assert "if mode_id != 0x0200 {" in s
        assert "bus.tx.ttl_ms = K6MB_TTL_MS;" in s
        assert "bus.tx.created_ms = 0;" not in s
        assert "bus.tx.ttl_ms = 0xFFFFFFFF;" not in s
        assert "bus.tx.created_ms = match mb_host_clock_ms()" in s
        assert s.count("mb_begin_tnc_phase(app, n);") == 1
        assert "unsafe { KEPT = true };" in s  # default arm preserved
        before = s
        # Registry and mailbox overlays have their own idempotence tests;
        # rerun ONLY the new dispatcher overlay to test its own identity.
        ov = ROOT / "firmware/k6-aura/apply-packet-dispatch.py"
        code = ov.read_text().replace(
            'Path(__file__).resolve().parent / "build/aura-fd6818b-port/src/app/pager.rs"',
            "Path(" + repr(str(pager_rs)) + ")")
        exec(compile(code, str(ov), "exec"), {})
        assert pager_rs.read_text() == before
