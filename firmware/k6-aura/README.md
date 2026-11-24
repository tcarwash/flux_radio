# Flux firmware for the Baofeng K6

This directory builds Flux for the AG7SU FD6818B radio: the native VFO/channel radio with a local module catalog. Scan images include broadcast FM and Explore rendering and scanner services. The native Search feature remains in the base image. Satellite and SSTV are omitted; they can be added later as modules. The installed module picker is labeled **CATALOG**.

The startup splash shows the Flux wordmark, AG7SU/K6 identity, build version, and optional user boot text configured in the System settings. **System > About** shows the radio identity, active feature set, and exact linked flash usage and free space.

## Build

Required tools: Rust with `thumbv6m-none-eabi`, clang, LLD, LLVM binutils, Python 3, and the local 16-byte module signing key. The upstream source repository and revision are pinned in `build-fd6818b.sh`; Flux patches and feature gating are applied to that pinned source during each build.

```sh
K6_MOD_KEY_FILE="$HOME/.config/flux/k6-mod.key" \
  sh firmware/k6-aura/build-fd6818b.sh
```

The output directory is `firmware/k6-aura/out-fd6818b/`. The main artifact is `flux-fd6818b.bin`; module bundles are emitted beside it. Never commit the signing key or generated binary files.

The feature-pack builder and matrix use the same Flux base profile. The Scan pack delivers Explore through the local module catalog. Sotto remains a separate module/feature pack. Standalone Explore and pager images are reference-only and rejected by the FD6818B builder.

### Sotto development image

A separate **build-only** Sotto-1200 profile now lives on `dev/sotto-firmware`:

```sh
sh firmware/k6-aura/build-sotto.sh
python3 tools/k6_sotto_firmware_audit.py
```

It writes `out-fd6818b-sotto/flux-fd6818b-sotto.bin` without modifying the default image. The first-stage image contains the genuine Sotto-1200 packet/decoder and a bounded 33-word FDV1 guarded wire library; it **does not** enable unqualified B-chip RX/automatic TX or constitute a working two-way DV firmware. Flash margin is very small, and real-time CPU/SPI/PWM concurrency is not hardware-qualified. See [Sotto development firmware design and measurement report](../../docs/k6-sotto-firmware-dev.md) before flashing or adding services.
