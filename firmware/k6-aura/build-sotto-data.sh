#!/bin/sh
# Build-only Base + Data host for staged Sotto RF integration and live install.
# Not the full voice encoder/decoder profile. Never touches the radio.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
OUT=${1:-"$HERE/out-fd6818b-data4800-live-install"}
if [ -e "$OUT" ]; then echo "Choose a new output directory: $OUT" >&2; exit 1; fi
# Stack mode is required by the existing live library mutation service.
# Deliberately separate from the large-arena and Sotto decoder builds.
export K6_LIVE_SERIAL=1 K6_REMOTE_UI=1 K6_RAM0=stack K6_SERIAL_TRANSPORT=1
cd "$ROOT"
python3 tools/k6_pack_build.py base data --no-iq --output "$OUT"
printf '%s\n' 'Live-install development host built; no install or RF performed.'
