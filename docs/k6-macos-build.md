# Experimental macOS firmware builds

The Catalog, CLI and firmware build tools can run on macOS. Firmware still
targets the radio's Cortex-M0 MCU. Physical Base FCC flashing, calibration
preservation, VFO module installation/readback and module launch subsequently
passed on one Apple Silicon Mac and FD6818B radio. See the
[hardware checkpoint](macos-feasibility.md) for evidence and remaining limits.
The radio target remains FD6818B UV-K6/K61 hardware.

## Setup

Install Xcode Command Line Tools (or select an existing Xcode installation),
Git, Python 3.10+, Rust stable, and the full LLVM toolset. Homebrew's LLVM is
keg-only and LLD is packaged separately:

```sh
brew install llvm lld python
rustup target add thumbv6m-none-eabi
git submodule update --init --recursive
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
export PATH="$(brew --prefix llvm)/bin:$(brew --prefix lld)/bin:$PATH"
```

The host codebook generator uses `cc` from the selected Apple developer tools;
embedded objects use LLVM's `clang`. Check `clang --version`, `ld.lld --version`
and `rustup target list --installed` before building. Keep the virtual environment
active so the shell pipeline and module builder use its Python dependencies.

## Build and inspect

Choose the transmit profile appropriate for the intended radio installation;
the CE example below is also suitable for emulator-only build validation.

```sh
python tools/k6.py build base scan --tx-profile ce \
  --output firmware/k6-aura/out-fd6818b/macos-scan
python tools/k6.py inspect firmware/k6-aura/out-fd6818b/macos-scan
python -m sim.k6ide --host 127.0.0.1 \
  --image firmware/k6-aura/out-fd6818b/macos-scan/firmware.bin
```

These commands do not open a serial port. The workbench is available at
`http://127.0.0.1:5056`. Run the Catalog separately with `python tools/k6.py catalog`.
For cable use, enumerate ports with `python -m serial.tools.list_ports`
and select the actual `/dev/cu.*` device; do not copy Linux `/dev/ttyUSB*` examples.
Follow the [installation guide](manual/install.md) before hardware operations.

## Validation record

Initial build matrix validated 2026-11-19, source commit `391e3dd`, on macOS 15.8.3 / arm64:
Python 3.10.5, Homebrew LLVM/LLD 23.1.6, Rust/Cargo 1.99.0.

Each row used a fresh checkout of the pinned Aura revision. The full build,
pack inspection, starter ZIP creation, extracted-starter validator, and
2,000,000-instruction headless emulator run passed. Each emulator report had
`fault: null`. The application limit is 122,880 bytes.

These images predate the cable dispatcher fix (`cc2441b`). Successful startup
did not exercise the affected INFO handler; use the corrected source for radio
installation. The hardware checkpoint records the corrected Base FCC image.

| Starter | TX profile | Firmware bytes | Flash remaining |
| --- | --- | ---: | ---: |
| Base | CE | 97,156 | 25,724 |
| Base | FCC | 97,152 | 25,728 |
| Base + Scan | CE | 111,692 | 11,188 |
| Base + Scan | FCC | 111,688 | 11,192 |
| Base + Scan + Designer | CE | 119,348 | 3,532 |
| Base + Scan + Designer | FCC | 119,344 | 3,536 |

The Python and legacy shell C builders produced byte-identical archives and
codebooks for Scan enabled and disabled with this compiler. The clean Base +
Scan CE image also matched the image built before the manifest/checksum
portability changes. This is not a claim of byte identity with Linux builds
using different compiler versions.

The 73 selected tooling, installer, feature-pack and build-regression tests
passed. One cache-lock test required rerunning outside the Codex filesystem
sandbox. A broader suite probe stopped at five failures: 114 passed, 8 skipped,
1 expected failure, and native compilation errors in the APRS host fixtures
because ELF section names are not valid Mach-O section specifiers. The full
root suite is not yet validated on macOS.

A separate native-host compatibility change subsequently passed all 42
targeted loader, capability-proof, OTA-mode and APRS regression cases. It uses
Mach-O section annotations only for native Apple targets and selects the
native linker's dead-strip/export-list flags. The ARM VFO, SURF and Explore
Scan module bytes remained identical to the matrix artifacts. The same
targeted regressions are included in Linux and macOS tooling CI. The complete
root suite remains a separate validation target.

The separate `Experimental macOS starter firmware` workflow repeats the six
builds and archive/emulator checks. Its artifacts have a `flux-macos-` prefix;
the existing Linux release workflow remains the publication path. A workflow
definition in this branch is not evidence of a completed hosted CI run.

## Remaining limitations

- Physical USB flashing and VFO installation passed on the radio in the
  hardware checkpoint. The ordinary backup command needed a sequential-read
  workaround; backup reliability and restore/recovery still need work.
- Intel Macs, optional Sotto/OTA packs and Linux audio/service utilities were
  not validated by this starter matrix.
- Some existing native host tests assume ELF sections or GNU linker flags.
- The matrix used fresh generated Aura checkouts, matching CI. Reapplying
  existing overlay scripts to a reused generated tree changed the firmware
  output during local testing; repeat-build idempotency needs separate work.
- Unicorn 2.1.4 can crash when a sandbox denies its CPU cache-size query.
  The emulator passed from an unrestricted process; use an ordinary terminal
  instead of repeating the restricted execution. The initial crash and passing
  rerun were observed on this Mac.
- The native Windows orchestration port remains separate. macOS uses the
  existing POSIX firmware pipeline; the portable Python C builder is also
  available for direct testing.

Keep generated firmware, logs, calibration backups and signing keys out of Git.

### Cargo discovery in catalog builds

The firmware builder checks Rust before applying source patches. It uses
`K6_CARGO` (or `CARGO`) when set, then searches `PATH`, then
`$CARGO_HOME/bin` (default `~/.cargo/bin`). Windows uses `cargo.exe`.
An invalid explicit override fails rather than silently selecting another Cargo.
Use a full executable path for overrides, including paths containing spaces.
The selected executable is shared by font metadata and all firmware links.

Install Rust with rustup and install the firmware target with
`rustup target add thumbv6m-none-eabi`. Restart the catalog process after
changing its environment. Build-environment failures appear in the catalog's
compiler output before any firmware overlays are applied.
