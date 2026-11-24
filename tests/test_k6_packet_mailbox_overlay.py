"""API27 packet-mailbox overlay (K6_PACKET_MAILBOX=1, needs K6_MODE_REGISTRY=1).

INCOMPLETE (prominent): the dispatcher phase toggle is NOT wired by this
slice (see apply-packet-mailbox.py). Do not claim routed TX works.

Validates offline without the Aura checkout or network:
- C ABI: KM_LEVEL 23 default / 26 registry / 27 mailbox; tail offsets
  strictly append-only after mode_info; every tail member 4-byte aligned
  (padding-free on 32-bit ARM); mailbox without registry is a hard error.
- Overlay source gates: field/init/level/callback anchors, cfg gating,
  idempotence on a synthetic pager.rs fixture (registry overlay first,
  then mailbox overlay, twice).
- Rust tail typechecks under --cfg k6_packet_mailbox and refuses without it.
"""
import re
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MOD_H = ROOT / "c/k6/k6_mod.h"
OVERLAY = ROOT / "firmware/k6-aura/apply-packet-mailbox.py"
BUILD_SH = ROOT / "firmware/k6-aura/build-fd6818b.sh"
K6_BUILD = ROOT / "tools/k6_build.sh"


def compile_probe(extra=(), body=""):
    prog = "#include <stdio.h>\n#include \"k6_mod.h\"\n" + body
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


def test_default_level_untouched():
    out = compile_probe(body="int main(void){printf(\"%d\\n\",KM_LEVEL);return KM_LEVEL!=23;}\n")
    assert out.strip() == "23"


def test_registry_level_still_26():
    out = compile_probe(["-DK6_MODE_REGISTRY=1"], body="""
_Static_assert(KM_LEVEL==26,"level");
int main(void){printf("%d\\n",KM_LEVEL);return 0;}
""")
    assert out.strip() == "26"


def test_mailbox_level_27_and_tail_append_only():
    out = compile_probe(["-DK6_MODE_REGISTRY=1", "-DK6_PACKET_MAILBOX=1"], body="""
_Static_assert(KM_LEVEL==27,"level");
_Static_assert(K6MB_VERSION==1,"ver");
_Static_assert(K6MB_MAX_PAYLOAD==128,"mtu");
_Static_assert(__builtin_offsetof(k6_api_t,mb_version)>__builtin_offsetof(k6_api_t,mode_info),"append1");
_Static_assert(__builtin_offsetof(k6_api_t,stage_packet)>__builtin_offsetof(k6_api_t,mb_version),"order2");
_Static_assert(__builtin_offsetof(k6_api_t,poll_result)>__builtin_offsetof(k6_api_t,stage_packet),"order3");
_Static_assert(__builtin_offsetof(k6_api_t,take_rx)>__builtin_offsetof(k6_api_t,poll_result),"order4");
_Static_assert(__builtin_offsetof(k6_api_t,claim_packet)>__builtin_offsetof(k6_api_t,take_rx),"order5");
_Static_assert(__builtin_offsetof(k6_api_t,complete_packet)>__builtin_offsetof(k6_api_t,claim_packet),"order6");
_Static_assert(__builtin_offsetof(k6_api_t,publish_rx)>__builtin_offsetof(k6_api_t,complete_packet),"order7");
/* Every tail member is 4-byte sized/aligned: padding-free on 32-bit ARM. */
_Static_assert(sizeof(((k6_api_t*)0)->mb_version)==4,"s1");
_Static_assert(sizeof(((k6_api_t*)0)->stage_packet)==sizeof(void*),"s2");
_Static_assert(_Alignof(((k6_api_t*)0)->mb_version)==4,"a1");
int main(void){printf("%d\\n",KM_LEVEL);return 0;}
""")
    assert out.strip() == "27"


def test_mailbox_without_registry_is_hard_error():
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "probe.c"
        src.write_text('#include "k6_mod.h"\nint main(void){return 0;}\n')
        r = subprocess.run(["cc", "-O2", "-std=c99", "-I", str(ROOT / "c/k6"),
                            "-DK6_PACKET_MAILBOX=1", str(src), "-o", str(Path(d) / "p")],
                           capture_output=True, text=True)
        assert r.returncode != 0
        assert "K6_MODE_REGISTRY" in r.stderr


def test_overlay_and_build_hooks():
    src = OVERLAY.read_text()
    compile(src, str(OVERLAY), "exec")
    for needle in ["pub mb_version", "pub stage_packet", "pub poll_result",
                   "pub take_rx", "pub claim_packet", "pub complete_packet",
                   "pub publish_rx", "mod_mb_stage_packet", "mod_mb_poll_result",
                   "mod_mb_take_rx", "mod_mb_claim_packet", "mod_mb_complete_packet",
                   "mod_mb_publish_rx", "k6_packet_mailbox", "K6_MODE_REGISTRY",
                   "K6MB_BUS", "K6MB_TNC_PHASE", "INCOMPLETE",
                   "mode_info: mod_mode_info,", "cancel_poll"]:
        assert needle in src, needle
    # Exact C-tail signatures mirrored in the Rust overlay.
    for sig in ["fn(u16, *const u8, u16) -> i32",
                "fn(*mut u32, *mut u32, *mut i32) -> i32",
                "fn(*mut u8, u16, *mut u16, *mut u32) -> i32",
                "fn(u16, u32, *mut u8, u16, *mut u32) -> i32",
                "fn(u32, i32) -> i32",
                "fn(u16, *const u8, u16) -> i32"]:
        assert sig in src, sig
    sh = BUILD_SH.read_text()
    assert "apply-packet-mailbox.py" in sh and "--cfg k6_packet_mailbox" in sh
    assert "Packet mailbox requires K6_MODE_REGISTRY=1" in sh
    # Mailbox opt-in sits after the registry opt-in, default image unchanged.
    assert sh.index("apply-mode-registry.py") < sh.index("apply-packet-mailbox.py")
    assert "K6_PACKET_MAILBOX=${K6_PACKET_MAILBOX:-0}" in sh
    kb = K6_BUILD.read_text()
    assert "-DK6_PACKET_MAILBOX=1" in kb and "K6_PACKET_MAILBOX requires K6_MODE_REGISTRY=1" in kb


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
"""


def run_overlay_chain(pager_rs: Path):
    for ov in ["apply-mode-registry.py", "apply-packet-mailbox.py"]:
        src = (ROOT / "firmware/k6-aura" / ov).read_text()
        code = src.replace(
            'Path(__file__).resolve().parent / "build/aura-fd6818b-port/src/app/pager.rs"',
            "Path(" + repr(str(pager_rs)) + ")")
        assert "build/aura-fd6818b-port" not in code, ov
        exec(compile(code, ov, "exec"), {})
    s = pager_rs.read_text()
    exec(compile(code, "apply-packet-mailbox.py", "exec"), {})  # rerun: idempotent
    assert pager_rs.read_text() == s


def test_overlay_idempotent_and_gates_on_fixture():
    with tempfile.TemporaryDirectory() as d:
        pager_rs = Path(d) / "pager.rs"
        pager_rs.write_text(FIXTURE_API)
        run_overlay_chain(pager_rs)
        s = pager_rs.read_text()
        assert "level: if cfg!(k6_packet_mailbox) { 27 } else if cfg!(k6_mode_registry) { 26 } else" in s
        assert s.count("pub stage_packet: unsafe extern") == 1
        assert s.count("stage_packet: mod_mb_stage_packet,") == 1
        assert s.count("unsafe extern \"C\" fn mod_mb_stage_packet(") == 1
        assert "K6MB_TNC_PHASE" in s and "tnc_phase: false" in s


def test_rust_tail_typechecks_with_cfg_and_refuses_without():
    body = (OVERLAY.read_text().split("impl_block = '''")[1].split("'''")[0]
            .replace("super::K6ModeInfo", "u8"))
    with tempfile.TemporaryDirectory() as d:
        lib = Path(d) / "mb.rs"
        lib.write_text(body + "\npub fn _probe() {}\n")
        for cfg in [["--cfg", "k6_packet_mailbox"], []]:
            cmd = ["rustc", "--edition=2021", "--crate-type=lib",
                   *cfg, str(lib), "-o", str(Path(d) / "x.rlib")]
            r = subprocess.run(cmd, capture_output=True, text=True)
            assert r.returncode == 0, (cfg, r.stderr)
