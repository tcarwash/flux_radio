#!/bin/sh
# Flux Sotto-1200 development firmware target for FD6818B (K6 B-chip).
#
# Builds ONLY. Does not flash a radio, use serial/PTT/SDR, install modules
# or modify a physical K6's SPI flash.
#
# Usage: sh firmware/k6-aura/build-sotto.sh
#
# The fixed-point Sotto-1200 decoder and existing pager/playback service
# are linked, but their native B-chip RF RX path is not yet enabled.
# Autonomous mic encode, B-chip packet receive and two-way live DV are
# development work in progress; do not treat this image as RF qualified.
#
# With the current flash budget, the Sotto variant excludes legacy Scan
# and native FM, uses an ASCII font subset, and compiles the decoder/math
# for size rather than peak throughput. It retains Flux UI, live serial,
# virtual keys/screenshots, the module catalog and the independent TX watchdog.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)

# Do not allow ambient environment variables to silently swap out the
# codec or hardware service controls for a supposedly Sotto-dev image.
if [ -n "${K6_CODEC:-}" ] && [ "$K6_CODEC" != 1200 ]; then
    echo "Sotto development target requires K6_CODEC=1200" >&2
    exit 2
fi
if [ "${K6_FSK_RX_SERVICE:-0}" != 0 ]; then
    echo "Experimental B-chip FSK RX must be qualified separately" >&2
    exit 2
fi
if [ "${K6_OTA_SERVICE:-0}" != 0 ]; then
    echo "Sotto development target remains cable-only" >&2
    exit 2
fi
if [ "${K6_LEAN_TXDOG:-1}" != 1 ]; then
    echo "Sotto live-serial target requires the independent TX watchdog" >&2
    exit 2
fi

# Leave the normal Flux image and its existing output directory unchanged.
K6_SOTTO_SERVICE=1
K6_CODEC=1200
K6_DV_WIRE=1
K6_SOTTO_DEC_OPT=-Oz
K6_SOTTO_MATH_OPT=-Oz
# Keep only the overlap-add DSP loop at -O2. -Oz everywhere else
# breaks the modeled 96MHz/20ms deadline on the recorded test clip.
K6_SOTTO_OA_FAST=1
K6_LIVE_SERIAL=1
K6_REMOTE_UI=1
K6_LEAN_TXDOG=1
K6_FSK_RX_SERVICE=0
K6_OTA_SERVICE=0
K6_MODULE_REMOVE=1
K6_SCAN_SERVICE=0
K6_NATIVE_FM=0
K6_FONT_SUBSET=1
K6_THEME=flux
K6_AURA_FEATURES=blowfang-host,fd6818b,lean,spilib,txdog-quiet
K6_IMAGE_NAME=flux-fd6818b-sotto
K6_OUT="$HERE/out-fd6818b-sotto"
export K6_SOTTO_SERVICE K6_CODEC K6_DV_WIRE K6_SOTTO_DEC_OPT K6_SOTTO_MATH_OPT K6_SOTTO_OA_FAST
export K6_LIVE_SERIAL K6_REMOTE_UI K6_LEAN_TXDOG
export K6_FSK_RX_SERVICE K6_OTA_SERVICE K6_MODULE_REMOVE
export K6_SCAN_SERVICE K6_NATIVE_FM K6_FONT_SUBSET K6_THEME
export K6_AURA_FEATURES K6_IMAGE_NAME K6_OUT

echo "Building Flux Sotto-1200 development image (offline only)"
sh "$HERE/build-fd6818b.sh"
# Freeze the exact linked ELF next to its output binary, so the audit never
# accidentally tests another branch/profile from the mutable Cargo cache.
cp "$HERE/build/aura-fd6818b-port/target/thumbv6m-none-eabi/release/aura" \
   "$K6_OUT/flux-fd6818b-sotto.elf"
python3 "$HERE/../../tools/k6_sotto_firmware_audit.py" \
   --out "$K6_OUT" --elf "$K6_OUT/flux-fd6818b-sotto.elf" \
   > "$K6_OUT/build-audit.json"
echo "Sotto build-only ELF/image audit PASS (not authorization to flash)"
