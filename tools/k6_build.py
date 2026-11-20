#!/usr/bin/env python3
"""Portable C-library builder for the Flux FD6818B host.

The supported path is the current no-voice host (SPI codebook, with or
without Scan). Legacy voice/benchmark configurations retain k6_build.sh.
"""
import os
from pathlib import Path
import shutil
import tempfile
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def source_tree(root, staging):
    """Materialize the known Sotto links only if Git checked them out as text."""
    c = root / 'c'
    if (c / 'fixed/fx.h').is_file() and (c / 'sotto.h').is_file() and (c / 'sotto.h').read_bytes() != b'../sotto/c/sotto.h':
        return c
    sotto = root / 'sotto/c'
    if not (sotto / 'fixed/fx.h').is_file():
        raise ValueError('Sotto source missing: run git submodule update --init --recursive')
    target = Path(staging) / 'c'
    shutil.copytree(c / 'k6', target / 'k6')
    shutil.copytree(sotto / 'fixed', target / 'fixed')
    for name in ('sotto.h', 'sotto_internal.h', 'sotto_lr_dec.inc', 'sotto_lr_tables.h',
                 'sotto_vlr.h', 'sotto_vlr_dec.inc', 'sotto_vlr_tables.c', 'sotto_vlr_tables.h'):
        shutil.copyfile(sotto / name, target / name)
    return target


def build(out, extra=(), env=None):
    with tempfile.TemporaryDirectory(prefix='flux-c-') as staging:
        return _build(out, extra, env, source_tree(ROOT, staging))


def _build(out, extra, env, c):
    env = os.environ.copy() if env is None else env.copy()
    if env.get('K6_VOICE', '1') != '0' or env.get('K6_CB') != 'spi' or env.get('K6_CODEC'):
        raise ValueError('portable builder currently requires K6_VOICE=0 K6_CB=spi and no K6_CODEC')
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    cc = env.get('K6_CC', 'clang')
    ar = env.get('K6_AR', 'llvm-ar')
    objcopy = env.get('K6_OBJCOPY', 'llvm-objcopy')
    hostcc = env.get('HOSTCC', 'clang' if os.name == 'nt' else 'cc')

    def run(*args, capture=False):
        return subprocess.run([str(a) for a in args], cwd=ROOT, env=env,
                              check=True, text=True, stdout=subprocess.PIPE if capture else None)

    resource = run(cc, '-print-resource-dir', capture=True).stdout.strip()
    flags = ['--target=thumbv6m-none-eabi', '-mcpu=cortex-m0', '-mthumb',
             '-mfloat-abi=soft', '-nostdinc', '-isystem', c / 'fixed/cm4/inc',
             '-isystem', Path(resource) / 'include', '-ffunction-sections',
             '-fdata-sections', '-std=c99', '-ffreestanding', '-fno-builtin-calloc',
             '-fno-builtin-free', '-Wall', '-Wno-unused-function', '-DSOTTO_V36',
             '-DSOTTO_PITCH_THIRD', '-DFX_CS512_CONST', '-DFX_MP_ROWGEN',
             '-DFX_SYNTH_STATE', '-DFX_YIELD_HOOK', '-I' + str(c), '-O2']
    if env.get('K6_PKT_YIELD', '1') != '0':
        flags.append('-DSP_PKT_YIELD')
    flags.extend(['-DSP_DEC_POOL', *extra])
    helper = out / ('cb_image.exe' if os.name == 'nt' else 'cb_image')
    run(hostcc, '-O2', '-std=c99', '-I' + str(c),
        c / 'k6/cb_image.c', c / 'k6/sp_cb.c',
        c / 'fixed/fx_tables.c', '-o', helper)
    run(helper, out / 'sotto-cb.bin')
    crc = run(helper, '-c', capture=True).stdout.strip()
    flags.extend(['-DFX_CB_SPI', '-DSP_CB_CRC=' + crc])
    keyflags = []
    if env.get('K6_OTA_SERVICE', '1') == '0':
        keyflags.append('-DKM_CABLE_ONLY')
    elif env.get('K6_MOD_KEY_FILE'):
        key = bytes.fromhex(Path(env['K6_MOD_KEY_FILE']).read_text().strip())
        if len(key) != 16:
            raise ValueError('module key must contain 32 hex digits')
        keyflags.append('-DKM_KEY=' + ','.join(map(str, key)))

    def compile(src, name=None, *opts):
        src = c / src
        dest = out / (name or (src.stem + '.o'))
        # Avoid printing private key compiler arguments on failure.
        try:
            run(cc, *flags, *opts, '-c', src, '-o', dest)
        except subprocess.CalledProcessError:
            raise RuntimeError(f'C compilation failed: {src.name}') from None
        return dest

    compile('k6/k6_mod.c', None, '-Oz', *keyflags)
    for name in ('k6_lib', 'user_store', 'system_page', 'card_record', 'card_store', 'card_air'):
        compile(f'k6/{name}.c', None, '-Oz', *(['-DKM_USER_STORE'] if env.get('K6_USER_STORE') == '1' else []))
    compile('k6/sotto_pager.c', None, env.get('K6_PAGER_OPT', '-Oz'))
    # Preserve the original archive order and all intermediate objects.
    compile('fixed/sotto_fx_dec.c', None, '-Dcalloc=sp_calloc', '-Dfree=sp_free')
    compile('fixed/fx_math.c')
    compile('fixed/fx_tables.c')
    for name in ('sp_cb', 'sp_vlr'):
        compile(f'k6/{name}.c', None, '-Oz')
    if env.get('K6_RAMFUNC') == '1':
        run(objcopy, '--rename-section', '.text.fx_oa_block=.data.ramfunc.fx_oa_block,alloc,load,contents,code', out / 'fx_math.o')
    compile('k6/ifpager.c', None, '-Os')
    for name in ('xscan', 'xscan_hot', 'xlisten', 'explore', 'cwid'):
        compile(f'k6/{name}.c', None, '-Oz')
    host = env.get('K6_EXPLORE_HOST', '0') == '1'
    if host:
        symbols = 'defaults mem_bytes init step resume close summary state_bytes info up tone level'.split()
        compile('k6/xscan.c', 'xscan_modhost.o', '-Oz', '-DXS_HOT=4', '-DXS_COLS=1',
                '-DXS_MODULE_NO_PEEK', '-DXS_MODULE_NO_SWEEP_DIAG', '-DXS_MODULE_NO_REFINE',
                *[f'-Dxs_{s}=kmx_xs_{s}' for s in symbols])
    compile('k6/rt.c', 'k6_rt.o', '-Oz')
    compile('k6/sted.c', None, '-Oz')
    compile('k6/xdmr.c', None, '-Os')
    for page in ('SWEEP', 'LISTEN', 'TABLE', 'EDIT'):
        compile('k6/kp_tab.c', f'kp_{page.lower()}.o', f'-DKP_PAGE=KP_{page}')
    names = 'sotto_pager k6_mod sp_novoice sp_cb sp_vlr fx_tables ifpager xscan xscan_hot xlisten xdmr explore cwid k6_rt sted'.split()
    if host:
        if env.get('K6_SCAN_SERVICE', '1') == '1':
            names.append('xscan_modhost')
        else:
            compile('k6/scan_host_unavailable.c', None, '-Oz')
            names.append('scan_host_unavailable')
    names.extend(('k6_lib', 'user_store', 'system_page', 'card_record', 'card_store', 'card_air'))
    compile('k6/sp_novoice.c', None, '-Oz')
    for name in ('vfo_mode_probe', 'scheduled_dispatch'):
        compile(f'k6/{name}.c', None, '-Oz')
        names.append(name)
    # The host pack does not normally use external objects. Accept explicitly
    # passed paths, rather than splitting a Windows path on whitespace.
    if env.get('K6_LIBEXTRA'):
        raise ValueError('K6_LIBEXTRA is not supported by the portable host builder')
    compile('k6/k6_mem.c', None, '-Oz', '-fno-builtin')
    archive = out / 'libsotto_k6.a'
    archive.unlink(missing_ok=True)
    run(ar, 'rcs', archive, *[out / (name + '.o') for name in names], out / 'k6_mem.o')
    print(f'built {archive}')
    return archive


if __name__ == '__main__':
    try:
        build(os.environ.get('K6_OUT', ROOT / 'c/k6/build-explore'), sys.argv[1:])
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        sys.exit(str(exc))
