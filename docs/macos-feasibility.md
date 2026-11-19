# macOS feasibility and hardware checkpoint

Checkpoint: 2026-11-19. Fork: <https://github.com/finitor/flux_radio>.
Upstream baseline: `b0e2546`.

## Result

The core workflow works on the tested Apple Silicon Mac: build firmware,
flash a physical FD6818B radio over USB, install a module through the browser
Catalog, read it back, and launch it on the radio. This replaces the initial
assessment at `b1f5a8a`, which had not exercised physical USB or a local firmware
build.

The complete physical workflow used two OS-independent fixes proposed separately:
[cable dispatch](https://github.com/tcarwash/flux_radio/pull/2) and
[EXPLORE Try audio](https://github.com/tcarwash/flux_radio/pull/1).
The macOS support branch records those hardware results but does not include
either fix; apply them separately when reproducing the full radio workflow.

Test host: macOS 15.8.3 / arm64, Python 3.10.5, Homebrew LLVM/LLD 23.1.6,
Rust/Cargo 1.99.0. See [macOS setup and build instructions](k6-macos-build.md).
The firmware still targets the radio's Cortex-M0 MCU.

## Physical-radio evidence

The test radio initially displayed factory firmware V0.06. After installing
Flux Base FCC, its System CHIPID screen reported **`0x4829` (FD6818B)**.
This is evidence for this radio, not a mapping of all V0.06 radios to FD6818B.

| Operation | Observed result |
| --- | --- |
| USB enumeration | Cable appeared as a `/dev/cu.usbserial-*` device; USB VID/PID `1a86:7523` |
| First firmware flash | Base FCC image acknowledged by the UPDATE bootloader |
| SPI backup before first format | Complete 2 MiB dump saved privately; calibration sector reread and matched |
| First boot | User completed the SPI format prompt and reached the radio UI |
| Initial VFO installation | Stopped at INFO before uploading; exposed the cable dispatcher bug below |
| Corrected firmware flash | Base FCC image flashed through UPDATE; no second SPI format |
| Corrected cable queries | Capability format 2, cable-only, API ceiling 23; INFO protocol 2, 32 slots, 11,776-byte upload arena; replay watermark 0 |
| Calibration after corrected flash | Sector `0xF000..0x10000` matched the saved backup |
| Browser Catalog installation | `org.blowfang.k6.vfo` automatically installed in slot 16 |
| Independent module readback | All installed fields except the changing upload serial/tag matched the bundle; installed CRC verified |
| Module launch | User reached `BLOWFANG VFO A` through MENU → CATALOG → VFO RADIO and reported it working |

The installed module image is **4,829 bytes** including its library metadata.
The log's decimal `r=4829` is a byte count, distinct from hexadecimal CHIPID
`0x4829`. VFO RADIO is a receive-only utility; voice PTT is supplied by the
native radio screen rather than this module.

The original backup remains private and outside Git. This backup used
single-request reads with a 0.5-second response timeout after the ordinary
backup command failed partway through. The ordinary backup command therefore
still needs a reliability fix; a successful workaround is not validation of
its default transfer settings. Restoring the backup was not tested.

## Scan and EXPLORE audio follow-up

A subsequent Base + Scan / All locked / Flux build (111,680 bytes) passed
artifact inspection and both compiled cable/install emulator checks. The GUI
flashed it and verified VFO in slot 16, SURF in slot 17 and EXPLORE in slot 18.
The user confirmed FLOCK remained ALL LOCK. SURF produced hiss in monitor mode,
received stations and visibly refreshed its spectrum with monitor disabled.

EXPLORE detected candidates but its Try action was silent. Commit `6b8b84f`
opens FM audio, the speaker and squelch during Try, then restores their previous
state before scanning resumes. Four Cortex-M0 regression cases reproduced the
missing speaker enable before the fix and passed afterwards, including cleanup
on key exit and power-off. The corrected module (3,522 installed bytes) replaced
slot 18 with verified cable readback. After unplugging the cable, the user
confirmed Try now produced sound. This confirms audible output, not the identity
or intelligibility of every detected signal or receiver sensitivity measurements.

## Cable dispatcher fix

`apply-capability-query.py` emitted a `CMD_CABLE_CAPS` match arm without declaring
its constant. Rust accepted the identifier as a binding, so requests with address
zero and payload length zero or sixteen entered the capability handler regardless
of opcode. An INFO request consequently received `K6CP` instead of `BLWF`.

Commit `cc2441b` declares opcode `0x49`, asserts idempotent constant generation,
and adds compiled-firmware regressions for distinct CAPS/INFO/SERIAL/error/END
responses plus cable-only module installation with readback. The old image
reproduced the exact physical-radio INFO failure in the emulator; the corrected
image passed both compiled regressions and then the physical checks above.
The independent cable-fix PR adds these regressions to all six Linux starter builds.

The physical test image was built from `b0e2546` plus this fix, before the
integration branch was assembled:

- Profile: Base FCC, cable-only, 97,120 bytes.
- Firmware SHA-256: `7fc65e55adc8b6ae61722f17243e2dea253d9a588b0dee1af8532e4428c9a718`.
- Reported linker build ID: `0a4458353ad8d5c8` (reported identity, not attestation).
- Bundled VFO binary SHA-256: `d143a6ff1c1878c58aba4c7db2865fcece3a5341bb17b4ab41bf329043d71a4c`.

## Review and upstream strategy

The topic branches remain available independently. `main` remains the upstream
baseline; the integration branch provides a convenient checkpoint without
rewriting the smaller branches.

| Branch | Purpose and dependency |
| --- | --- |
| `codex/macos-c-builder` | Portable C-builder validation; based on upstream |
| `codex/macos-firmware-build` | POSIX build portability, setup documentation and macOS starter CI; stacked on C-builder validation |
| `codex/macos-host-tests` | Native Mach-O annotations and platform-specific host linking; stacked on firmware builds |
| `codex/cable-capability-dispatch` | Hardware-discovered firmware bug fix; independently based on upstream |
| `codex/macos-hardware-checkpoint` | Integration of the macOS stack and cable fix, plus this validation record |
| `codex/macos-support` | Single macOS PR: the portability stack and validation documentation; no independent radio fixes |
| `codex/explore-try-audio-upstream` | OS-independent EXPLORE audio PR; directly based on upstream |

Review one macOS PR and one PR for each independent radio fix. Each PR targets
upstream main directly and can merge without the other two. The smaller macOS
topic branches remain available to inspect individual implementation steps;
they are combined in the macOS PR rather than submitted as additional PRs.
Integrate accepted upstream commits into the checkpoint branch with merges;
avoid rewriting published review branches merely to refresh the checkpoint.
Do not submit the integration branch as an additional copy of accepted changes.

## Validation scope and remaining work

The earlier six-profile macOS matrix passed native builds, pack inspection,
starter ZIP validation and 2,000,000 emulator instructions per image. It predates
the dispatcher fix and did not establish cable correctness. Its exact results
are recorded in [the build guide](k6-macos-build.md#validation-record).
The selected 73 tooling/build tests and 42 native-host regressions passed during
that work. The integrated source-generation tests also passed (7 cases).
These are selected checks, not a complete root-suite pass.

Before opening the separate PRs, the isolated macOS support branch passed a
combined set of 105 tooling, portable-build, installer and native-loader/APRS
regressions. The independent cable and audio branches separately passed five
and four selected cases respectively.

Remaining validation includes systematic RF measurements and transmission,
recovery/backup restore, physical Designer and optional Sotto/OTA packs, Intel Macs, and the
complete root test suite. Hosted CI has been configured but successful runs for
these topic changes have not been established at this checkpoint.

The Linux PulseAudio/systemd utilities require a separate Mac audio/service port.
Reapplying overlays to a reused generated Aura tree also needs an idempotency
review; the six-profile matrix used fresh checkouts. Unicorn's cache-size query
crashed in the restricted Codex sandbox on this host and worked outside it; use
an ordinary terminal for emulator runs until that runtime issue is addressed.

Keep firmware outputs, emulator images, device backups, local module bundles,
configuration and signing keys out of Git. Commit source, tests, workflows and
reviewable validation records.
