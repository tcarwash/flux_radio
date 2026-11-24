# Flux Sotto-1200 development firmware (FD6818B)

**Branch:** `dev/sotto-firmware` — first development-stage, **buildable firmware target**, not an approved over-the-air digital voice release.

This integrates the existing Flux Sotto runtime with the research-proven 33-word guarded digital voice **wire-format** primitives, on top of the normal module-capable Flux host. Its new C receive/packet APIs are deliberately inert until the B-chip hardware driver, session manager, and audio scheduler are developed and accepted separately.

## Build the new target

```sh
git submodule update --init sotto
sh firmware/k6-aura/build-sotto.sh
python3 tools/k6_sotto_firmware_audit.py

# Run actual compiled Thumb decoder on a user-owned 8kHz Sotto-1200
# compressed .bit file. Never commit the user's microphone data.
# Cached Unicorn/Capstone and NumPy are needed for this offline test:
python3 tools/k6_sotto_firmware_emu.py \
  --elf firmware/k6-aura/out-fd6818b-sotto/c-build/bench.elf \
  --codec /path/to/private/voice.sotto1200.bit
```

The firmware image is **`firmware/k6-aura/out-fd6818b-sotto/flux-fd6818b-sotto.bin`** with a separate SHA256 checksum, exact linked `flux-fd6818b-sotto.elf` and `build-audit.json`. The normal build, its default `K6_SOTTO_SERVICE=0`, and its `out-fd6818b` artifacts are not touched by the Sotto wrapper. Nothing in these commands transmits, operates the radio, or flashes it.

**Deployment prerequisites, not performed by this build:** The selected Sotto profile expects its separate **30,720-byte external SPI codebook** (`out-fd6818b-sotto/c-build/sotto-cb.bin` has a 32-byte header plus this data), whose on-radio provisioning and CRC must be checked under the queue before any actual playback. The module catalog still needs the user's original module signing key (`K6_MOD_KEY_FILE`) to execute signed modules; a no-key build intentionally refuses module execution. No signing keys or private microphone audio belong in Git.

`build-sotto.sh` deliberately freezes the development profile:

| Setting | Value | Reason |
| --- | --- | --- |
| Radio / host | `fd6818b,blowfang-host,lean,spilib,txdog-quiet` | Standard Flux VFO, catalog, K6 module host |
| `K6_CODEC` | `1200` | Actual 6-byte / 40ms Sotto-1200 superframes |
| `K6_SOTTO_SERVICE` | `1` | Links Sotto voice packet/pager and fixed-point decoder/playback |
| `K6_DV_WIRE` | `1` | Includes 33-word L64 guard/FDV1 protocol parser and transmit queue **in C archive** |
| `K6_LIVE_SERIAL` / `K6_REMOTE_UI` | `1` / `1` | Keep serial CAT/module workflows and screenshot/virtual key support |
| `K6_LEAN_TXDOG` | `1` | Independent physical transmitter watchdog remains compiled |
| `K6_FSK_RX_SERVICE` | `0` | B-chip native packet RX **not** yet integrated/qualified in this firmware |
| `K6_OTA_SERVICE` | `0` | Keep the first image cable-only |
| `K6_SCAN_SERVICE` / `K6_NATIVE_FM` | `0` / `0` | Space for Sotto; some non-DV features deferred, not silently redefined |
| `K6_FONT_SUBSET` / `K6_THEME` | `1` / `flux` | Retain Flux UI with smaller ASCII glyph table |
| `K6_SOTTO_DEC_OPT` | `-Oz` | Compact fixed-point decoder code |
| `K6_SOTTO_MATH_OPT` | `-Oz` | Compact math except one critical hot function |
| `K6_SOTTO_OA_FAST` | `1` | Compile only `fx_oa_block` at `-O2`; exact Sotto arithmetic, otherwise compact math |

Target selector refuses ambient attempts to replace codec, activate experimental native-FSK RX/OTA, or remove the TX watchdog. The pinned upstream Sotto submodule stays unmodified. `c/k6/sotto_fx_oa_fast.c` is a separate copy of the pinned upstream `FX_NARROW_MUL` overlap-add function; its arithmetic and decoded PWM bitstream were cross-checked against both the default O2 DSP and other compact builds, not rewritten.

## Integrated pieces and provenance

Existing Rust firmware feature/service overlay: `firmware/k6-aura/apply-sotto-service.py` and `build-fd6818b.sh`. No unproven, borrowed B-chip register writes are enabled.

- `c/k6/mod/dv_guard33_packet.h` — research `fa933fa6`, existing CRC16/PN9 Flux L64 `0xA9` voice packet plus sacrificial `0xA55A` guard, 33 FD6818B FIFO words. Physical FSK-13/14 previously independently recovered all packets in this framing.
- `c/k6/mod/dv_guard33_stream.h` — bounded, zero-heap two-slot 320ms Sotto packet queue, explicitly refuses lost frames, unconfirmed TX acks and message ID wrap. **In-memory scheduling only.**
- `c/k6/mod/dv_guard33_session.h` — proposed FDV1 `0xE2` BEGIN/END control packet, strict CRC and offset0/2 recovery. Physically UNPROVEN; previously qualified against real Cortex-M0 emulator and synthetic complex SDR IQ.
- `c/k6/sotto_dv_wire.h/.c` — first integration ABI for zero-allocation, guard-tolerant C voice 66B RX and session control, plus codec stream queue wrappers; compiled into `libsotto_k6.a` when `K6_DV_WIRE=1`. **No module is automatically transmitted.** The new ABI is **not yet called** from the Rust RFIC RX/TX interrupt handler; linking this library is a preparatory seam, not a finished native on-radio modem.
- Real linked Sotto symbols `sp_init`, `sp_packet`, `sp_play_start`, `sp_play_pwm`, `sotto_dec_create`, and `fx_oa_block` checked by `k6_sotto_firmware_audit.py`. The SPI flash codebook setup is in the C archive but runtime B-chip SPI flash timing/validation remains a separate hardware gate.

## Build and emulator qualification — 2026-11-23

Actual target is Cortex-M0, fixed KD32F328 application flash budget **122,880 bytes**. All bytes below refer to linked on-target application flash, **not** combined ELF+debug information or raw C object size.

| Trial | Flash used | Flash left | Compiled ARM Unicorn model: worst 20ms Sotto playback block |
| --- | ---: | ---: | ---: |
| Voice + full scanner, O2 DSP | 144,960 | -22,080 | Not benchmarked in that exact feature profile |
| Scanner/native FM off + font subset, O2 DSP | 128,200 | -5,320 | 83.70 MHz equivalent |
| Compact decoder -Oz, DSP math -O2 | 124,672 | -1,792 | 87.06 MHz equivalent |
| Decoder and math -Oz (Flux theme, live serial + remote UI) | 122,408 | 472 | **102.08 MHz**, exceeds 96 MHz CPU budget |
| Decoder -Oz, math -Os | 122,616 | 264 | **97.26 MHz**, exceeds 96 MHz CPU budget |
| **Chosen: -Oz + fast overlap-add -O2** | **122,840** | **40** | **90.11 MHz**, under model budget but only ~6% modeled CPU reserve |

The observed **peak is from the ONE archived, 1.04-second K6 microphone sample** (26 compressed Sotto-1200 superframes, 52 decoded 20ms blocks); this is not a comprehensive voice corpus or a proof that real 8kHz PWM/audio will run under interrupts. The exact same 52-block decoded PWM data SHA256 was independently produced by all the DSP builds tested: `dc0441f7f3c37ebd6cb885d7c701f4755935d6863f38b4d8de9e60a014b5b0f0`. No original private audio/codec bytes are in git.

The emulator uses actual linked Thumb/Cortex-M0 instructions, two flash wait states and flash-model SPI-codebook bytes. **Physical SPI latency, 8kHz sampler/PWM IRQ, LCD work, serial service, RF receive, RF/PA turnaround and concurrency are NOT modeled.** A peak 90.11MHz of 96MHz CPU leaves insufficient demonstrated margin for those other tasks: **do not flash as DV-ready**. The 40-byte flash budget is likewise unusable as headroom for new services. The first major next-version task must recover **kilobytes**, not tens of bytes, via resident firmware modularization and/or moving optional functionality into module/SPI service layers.

### Reproduction / CI gates

```sh
python3 -m pytest -q \
 tests/test_k6_sotto_dv_wire.py \
 tests/test_k6_sotto_pack.py \
 tests/test_k6_feature_packs.py \
 tests/test_k6_ota_modes.py

python3 tools/k6_sotto_firmware_audit.py
```

The C tests compile the functions for both host and actual Cortex-M0 target, cross-check 1–8 original Sotto frame slots and displaced/normal guards against Python `modem.pager`, compare FDV1 controls, validate corruption rejection, and ensure the normal feature gate is unchanged. The firmware audit refuses missing, altered or oversized binaries and checks actual linked decoder symbols and wire-archive exports. It is **a build audit, NOT a flashing approval**.

## Development roadmap

1. Create substantial flash headroom for the next Sotto firmware image (at least several KiB for explicit B-chip TX/RX driver/scheduler). Keep real-time DSP margin via reproducible Cortex-M0 audio corpus tests. Review RAM: SPI decoder state, message store, module arena, ISR stack and ring buffers.
2. Wire a **bounded native B-chip 66-byte FSK receive** path into the Rust firmware's RFIC ISR/foreground services, independent CRC/guard and queued voice/session parsing. Do not steal the TIM14 TX watchdog or modify RX exception remap without detailed audit. Validate RX-only before any RF-transmitting firmware.
3. Integrate physical Sotto decoder, PWM speaker and jitter buffer. No flash or RF without separate GitHub #11 hardware reservation/ACK, canonical serial-port lock, and rollback procedure.
4. Adapt the already-emulated incremental Sotto slim encoder and SPI codebook/deck API from `research/live-dv-guarded-segment-scheduler-20261123` **after** B-chip 8kHz PA11 capture/ISR CPU and RF TX turnarounds are proven. Existing FSK-13/14 native 2-FSK guard success and the user's later standalone end-to-end TX experiment should be registered independently as evidence, not assumed to prove autonomous on-radio recording or duplex voice.
5. Implement explicit FDV1 talk session, end-of-talk, long-call epoch transition, collision handling and PT T controls; B-chip real receive+speaker and two-radio calls only after independent score and safety review.

**Radio status:** No radio or SDR was accessed or flashed during this branch's build/bench run. Existing worktree `develop` was not touched. All physical benches remain under GitHub shared queue issue #11, separately from this firmware build branch.
