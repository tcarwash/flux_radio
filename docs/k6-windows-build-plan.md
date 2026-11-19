# Cross-platform firmware builder (Windows and macOS)

Goal: the same Flux Catalog and CLI can customize, compile and install firmware
on native Windows without WSL and on macOS. Shared infrastructure may land on
main while full native builds remain experimental and gated. Do not advertise
full support until native build and hardware validation pass.

## Milestones

1. Preserve existing Linux build behavior and add a portable orchestration
   backend. Keep pinned Aura revision, patch order, service gates, flash limit,
   About metadata relinks, build identity and artifact receipts unchanged.
2. Port the C library build: use argument arrays, explicit tool paths, native
   host codebook generator (Clang with a Windows SDK/MSVC environment, or an
   equivalent validated host compiler), LLVM ARM cross compiler and archiver.
   Keep legacy codec/benchmark shell entry points until their parity is tested.
3. Port Aura orchestration: Git checkout/patch operations, Python patch scripts,
   Cargo target and encoded Rust flags, llvm-size and objcopy. Handle Windows
   path separators, spaces, executable suffixes and line endings. Preserve
   POSIX-compatible manifest paths.
4. Add a toolchain doctor and guided setup for Git, Python, Rust
   thumbv6m-none-eabi, Clang/LLD/LLVM and the native host C compiler.
   Expose actionable errors in the Catalog; enable Build only when supported.
5. CI: Linux and Windows build Base, Scan and Designer+Scan in both TX profiles.
   Check image bounds, capability receipts, module ABI/size and extracted pack
   verification; compare equivalent builds with pinned compiler versions.
   Bit-for-bit identity is a goal, not a substitute for compatibility tests.
6. Validate on a Windows PC and compatible radio before removing experimental
   status. No live radio writes in CI.

## First slice

Portable build lock and platform-neutral staged pack paths. These are shared
infrastructure independent of the actual compilation backend. Native Windows
build remains gated until the backend and tools are verified.

## Contributor quick start (macOS)

Clone with submodules (`git clone --recurse-submodules https://github.com/tcarwash/flux_radio.git`).
On macOS, install Homebrew LLVM and Python 3.10+; use `brew install llvm python`
and `export PATH="$(brew --prefix llvm)/bin:$PATH"` to select LLVM rather than
Apple's system compiler for the embedded target. Install Rust separately if
working on Aura (`rustup target add thumbv6m-none-eabi`).

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt pytest
python -m serial.tools.list_ports
python -m pytest -q tests/test_k6_portable_c_build.py tests/test_k6_port_lock.py tests/test_k6_build_lock.py tests/test_k6_starter_install.py
K6_VOICE=0 K6_CB=spi K6_EXPLORE_HOST=1 K6_SCAN_SERVICE=1 K6_PKT_YIELD=0 K6_OTA_SERVICE=0 K6_OUT=/tmp/flux-c-build python tools/k6_build.py
```

The portable Python C-library builder is available for direct testing; the
existing full firmware pipeline still uses the legacy shell builder by default.
The command above produces an ARM static library and codebook, **not** a flashable
firmware image. Test both Scan settings (0 and 1). On Linux, the archive and
codebook matched the legacy shell builder byte-for-byte for both settings.
Native C-library compilation was validated on macOS 15.8.3 / arm64 with
Homebrew LLVM 23.1.6 and Python 3.10.5. Both Scan settings produced archives
and codebooks byte-for-byte identical to the legacy shell C builder using the
same compiler. The contributor smoke tests passed (15 tests), including both
Scan settings and an output directory containing spaces. This does not validate
physical-radio behavior. All six cable-only starter profiles have also been
built, packaged, inspected and executed in the emulator on this Mac; see the
[macOS build guide](k6-macos-build.md) for setup, measurements and limitations.
The full firmware orchestration still uses POSIX shell scripts; this does not
complete the native Windows port. Native Windows compiler execution and full
firmware builds remain unverified.

Next steps: validate the C builder on each host, port the Aura orchestration
and module compiler, then add full firmware matrix builds. Report host OS,
architecture, LLVM/Rust versions, commands and errors when filing issues.
