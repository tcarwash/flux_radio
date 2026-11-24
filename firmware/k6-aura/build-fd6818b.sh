#!/bin/sh
# Build the Flux firmware for the FD6818B radio.
# The legacy Blowfang FSK packet modem is disabled until ported and hardware-tested.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
REPO=${AURA_REPO:-https://github.com/sophiel-meow/bf-k61-firmware-aura}
REV=${AURA_FD6818B_REV:-c3aeb17aec0fd09dccf931b52f6c7d5dbf72e7ac}
WORK="$HERE/build/aura-fd6818b-port"
OUT=${K6_OUT:-"$HERE/out-fd6818b"}
FEATURES=${K6_AURA_FEATURES:-blowfang-host,fd6818b,lean,spilib,txdog-quiet}
IMAGE=${K6_IMAGE_NAME:-flux-fd6818b}
K6_THEME=${K6_THEME:-flux}
K6_TX_PROFILE=${K6_TX_PROFILE:-ce}
K6_FLUX_ART_DIR=${K6_FLUX_ART_DIR:-"$ROOT/flux-art-kit/flux-arrays"}
PYTHONPATH="$ROOT/tools" python3 -c 'import sys; from k6_feature_packs import validate_theme; validate_theme(sys.argv[1])' "$K6_THEME"
PYTHONPATH="$ROOT/tools" python3 -c 'import sys; from k6_feature_packs import validate_tx_profile; validate_tx_profile(sys.argv[1])' "$K6_TX_PROFILE"
K6_OTA_SERVICE=${K6_OTA_SERVICE:-0}
K6_SOTTO_SERVICE=${K6_SOTTO_SERVICE:-0}
# Screenshots and logical UI keys are part of the standard live serial
# interface; an explicit K6_REMOTE_UI=0 still disables them for tiny builds.
K6_REMOTE_UI=${K6_REMOTE_UI:-${K6_LIVE_SERIAL:-0}}
# Remote PTT is baseline live-serial CAT, not an optional addon. A crashed
# foreground loop must not hold RF indefinitely: require the independent
# TIM14 transmitter watchdog for every live-serial firmware build.
if [ "${K6_LIVE_SERIAL:-0}" = 1 ]; then
  if [ "${K6_LEAN_TXDOG:-1}" != 1 ]; then
    echo "Live serial remote PTT requires the independent TX watchdog" >&2
    exit 1
  fi
  case ",$FEATURES," in
    *,txdog,*|*,txdog-quiet,*) ;;
    *) echo "Live serial remote PTT requires the txdog Cargo feature" >&2; exit 1 ;;
  esac
  K6_LEAN_TXDOG=1
fi
export K6_THEME K6_TX_PROFILE K6_OTA_SERVICE K6_SOTTO_SERVICE

case "$FEATURES" in
  *[!a-zA-Z0-9_,-]*) echo "Use a comma-separated list of Cargo features." >&2; exit 1 ;;
esac
case ",$FEATURES," in
  *,pager-explore,*|*,pager-only,*|*,voice-explore,*|*,explore,*)
    echo "Standalone Explore/pager firmware is reference-only. Use the Flux host and catalog modules." >&2
    exit 1
    ;;
esac
case ",$FEATURES," in
  *,blowfang-host,*) ;;
  *) echo "FD6818B builds require the blowfang-host feature." >&2; exit 1 ;;
esac

# Fail before cloning or applying overlays; propagate one executable to all helpers.
K6_CARGO=$(python3 "$ROOT/tools/k6_toolchain.py" "$WORK")
CARGO="$K6_CARGO"
PATH="$(dirname "$K6_CARGO"):${PATH:-}"
export K6_CARGO CARGO PATH

if [ ! -d "$WORK/.git" ]; then
  git clone -q "$REPO" "$WORK"
  git -C "$WORK" checkout -q --detach "$REV"
fi
[ "$(git -C "$WORK" rev-parse HEAD)" = "$REV" ] || {
  echo "checkout at $WORK is not pinned to $REV; preserve it and choose another AURA_FD6818B_REV" >&2
  exit 1
}
if [ ! -f "$WORK/src/app/pager.rs" ]; then
  git -C "$WORK" apply "$HERE/aura-pager.patch"
  git -C "$WORK" apply "$HERE/aura-pager-fd6818b.patch"
fi
if ! grep -q 'read_bytes_checked' "$WORK/src/drivers/norflash.rs"; then
  git -C "$WORK" apply "$HERE/aura-norflash-timeout.patch"
fi
if ! grep -q 'mod_fsk_rx_start' "$WORK/src/app/pager.rs"; then
  git -C "$WORK" apply "$HERE/aura-fd6818b-fsk-api.patch"
fi
if ! grep -q 'MOD_EXIT_TO_AURA' "$WORK/src/app/pager.rs"; then
  git -C "$WORK" apply "$HERE/aura-exit-to-aura.patch"
fi
if ! grep -q 'Two-line message for full Aura builds' "$WORK/src/ui/cps.rs"; then
  git -C "$WORK" apply "$HERE/aura-full-radio.patch"
fi
python3 "$HERE/ensure-host-feature.py" "$WORK/Cargo.toml"
if ! grep -q 'PXK6 card data uses explicit commands' "$WORK/src/cps/mod.rs"; then
  git -C "$WORK" apply "$HERE/aura-card-cable.patch"
fi
# Normalize the generated API level before reapplying older overlay scripts.
python3 "$HERE/apply-configurable-fsk.py" --reset-level
python3 "$HERE/apply-fd6818b-fsk-tx-api.py"
python3 "$HERE/apply-fd6818b-afsk-module-api.py"
python3 "$HERE/apply-fd6818b-kiss-api.py"
python3 "$HERE/apply-module-settings-api.py"
python3 "$HERE/apply-surf-vfo-b.py"
python3 "$HERE/apply-module-run-diag.py"
python3 "$HERE/apply-managed-signal-api.py"
python3 "$HERE/apply-explore-header-api.py"
python3 "$HERE/apply-explore-engine-api.py"
python3 "$HERE/apply-vfo-repeater-hint.py"
python3 "$HERE/apply-cw-bk-in-only.py"
python3 "$HERE/apply-cw-hang-timing.py"
python3 "$HERE/apply-module-power-off.py"
python3 "$HERE/apply-cardlink-stack.py"
python3 "$HERE/apply-host-module-screen.py"
python3 "$HERE/apply-explore-ui-api.py"
python3 "$HERE/apply-action-beep.py"
python3 "$HERE/apply-aprs-position-api.py"
python3 "$HERE/apply-boot-app-stack.py"
python3 "$HERE/apply-cable-stack.py"
python3 "$HERE/apply-capability-query.py"
python3 "$HERE/apply-ota-service.py"
python3 "$HERE/trim-generated-host.py"
python3 "$HERE/apply-pack-service-gates.py"
K6_SOTTO_SERVICE="$K6_SOTTO_SERVICE" python3 "$HERE/apply-sotto-service.py"
if [ "${K6_MODULE_REMOVE:-1}" = 1 ]; then
  python3 "$HERE/apply-library-erase.py"
fi
case ",$FEATURES," in
  *,blowfang-host,*)
    python3 "$HERE/apply-blowfang-host.py"
    # Restore the legacy draw sequence before focus patch on a reused checkout.
    K6_THEME=flux python3 "$HERE/apply-theme-layers.py"
    python3 "$HERE/apply-vfo-focus.py"
    python3 "$HERE/apply-theme-meter.py"
    python3 "$HERE/apply-theme-frequency.py"
    python3 "$HERE/apply-theme-layers.py"
    python3 "$HERE/apply-theme-battery.py"
    python3 "$HERE/apply-band-surf-shortcut.py"
    python3 "$HERE/apply-status-callsign.py"
    ;;
esac
python3 "$HERE/apply-host-feature-trim.py"
python3 "$HERE/apply-small-arrows.py"
python3 "$HERE/apply-tx-profile.py"
python3 "$HERE/apply-module-key-actions.py"
python3 "$HERE/apply-module-loader-init.py"
python3 "$HERE/apply-vfo-mode-runtime.py"
python3 "$HERE/apply-vfo-selector.py"
python3 "$HERE/apply-scheduled-probe.py"
python3 "$HERE/apply-designer.py"
python3 "$HERE/apply-theme-selection.py"
python3 "$HERE/apply-theme-install.py"
K6_FLUX_ART_DIR="$K6_FLUX_ART_DIR" python3 "$HERE/apply-flux-art.py"
K6_AURA_FEATURES="$FEATURES" K6_SCAN_SERVICE="${K6_SCAN_SERVICE:-1}" K6_FSK_RX_SERVICE="${K6_FSK_RX_SERVICE:-0}" python3 "$HERE/apply-flux-branding.py"
python3 "$HERE/apply-theme-splash.py"
K6_FLUX_ART_DIR="$K6_FLUX_ART_DIR" python3 "$HERE/apply-raster-boot.py"
python3 "$HERE/apply-serial-lab.py"
python3 "$HERE/apply-compact-edit.py"
python3 "$HERE/apply-compact-format.py"
python3 "$HERE/apply-compact-settings.py"

python3 "$HERE/apply-lean-txdog.py"
python3 "$HERE/apply-el-power.py"
python3 "$HERE/apply-ram0.py"
python3 "$HERE/apply-system-partition.py"
python3 "$HERE/apply-system-demo.py"
python3 "$HERE/apply-system-install.py"
python3 "$HERE/apply-channel-page.py"
python3 "$HERE/apply-settings-page.py"
python3 "$HERE/apply-compact-channel-list.py"
python3 "$HERE/apply-configurable-fsk.py"
python3 "$HERE/apply-fm-budget-gate.py"
if [ "${K6_FONT_SUBSET:-0}" = 1 ]; then python3 "$HERE/apply-font-subset.py"; fi
python3 "$HERE/apply-live-serial.py"
K6_MODE_REGISTRY=${K6_MODE_REGISTRY:-0}
K6_MODE_RFTEST=${K6_MODE_RFTEST:-0}
K6_PACKET_MAILBOX=${K6_PACKET_MAILBOX:-0}
export K6_MODE_REGISTRY
if [ "$K6_MODE_REGISTRY" = 1 ]; then
  python3 "$HERE/apply-mode-registry.py"
fi
if [ "$K6_PACKET_MAILBOX" = 1 ]; then
  [ "$K6_MODE_REGISTRY" = 1 ] || { echo "Packet mailbox requires K6_MODE_REGISTRY=1" >&2; exit 1; }
  python3 "$HERE/apply-packet-mailbox.py"
  python3 "$HERE/apply-packet-dispatch.py"
fi
if [ "$K6_MODE_RFTEST" = 1 ]; then
  [ "$K6_MODE_REGISTRY" = 1 ] || { echo "RFTEST discovery requires K6_MODE_REGISTRY=1" >&2; exit 1; }
fi
if [ "${K6_REMOTE_UI:-0}" = 1 ]; then
  [ "${K6_LIVE_SERIAL:-0}" = 1 ] || { echo "Remote UI requires live serial" >&2; exit 1; }
  case ",$FEATURES," in
    *,blowfang-host,*) ;;
    *) echo "Remote UI requires the Flux host LCD framebuffer" >&2; exit 1 ;;
  esac
  python3 "$HERE/apply-remote-ui.py"
fi
grep -q 'FD6818B FSK TBD' "$WORK/src/ui/pager.rs" || {
  echo "FD6818B overlay is missing from $WORK; inspect that checkout before building" >&2
  exit 1
}

mkdir -p "$OUT"
cp "$K6_FLUX_ART_DIR/splash.bin" "$OUT/flux-boot-logo.bin"
K6_EXPLORE_HOST=0
case ",$FEATURES," in *,blowfang-host,*) K6_EXPLORE_HOST=1 ;; esac
K6_VOICE=0
if [ "$K6_SOTTO_SERVICE" = 1 ]; then
  K6_VOICE=1
fi
K6_EXPLORE_HOST="$K6_EXPLORE_HOST" K6_SCAN_SERVICE="${K6_SCAN_SERVICE:-1}" K6_PKT_YIELD=0 K6_VOICE="$K6_VOICE" K6_CB=spi \
  K6_OUT="$OUT/c-build" sh "$ROOT/tools/k6_build.sh"
mkdir -p "$WORK/lib"
cp "$OUT/c-build/libsotto_k6.a" "$WORK/lib/libsotto_k6.a"
PACK_CFG=""
if [ "$K6_MODE_REGISTRY" = 1 ]; then PACK_CFG="$PACK_CFG --cfg k6_mode_registry"; fi
if [ "$K6_PACKET_MAILBOX" = 1 ]; then PACK_CFG="$PACK_CFG --cfg k6_packet_mailbox"; fi
if [ "$K6_MODE_RFTEST" = 1 ]; then PACK_CFG="$PACK_CFG --cfg k6_mode_rftest"; fi
if [ "${K6_LIVE_SERIAL:-0}" = 1 ]; then PACK_CFG="$PACK_CFG --cfg k6_live_serial"; fi
if [ "${K6_REMOTE_UI:-0}" = 1 ]; then PACK_CFG="$PACK_CFG --cfg k6_remote_ui"; fi
if [ "${K6_USER_STORE:-0}" = 1 ]; then
  python3 "$HERE/apply-user-store.py"
  python3 "$HERE/apply-user-restore.py"
  PACK_CFG="$PACK_CFG --cfg k6_user_store"
fi
if [ "${K6_MEMORY_BANK:-0}" = 1 ]; then
  python3 "$HERE/apply-memory-bank.py"
  PACK_CFG="$PACK_CFG --cfg k6_memory_bank"
fi
if [ "${K6_CHANNEL_PAGE:-0}" = 1 ]; then
  [ "${K6_RAM0:-0}" = 1 ] && [ "${K6_SYSTEM_INSTALL:-0}" = 1 ] && [ "${K6_SYSTEM_DEMO:-0}" = 0 ] && [ "${K6_VFO_PROBE:-0}" = 0 ] && [ "$K6_SOTTO_SERVICE" = 0 ] || { echo "Channel page requires expanded cable-only arena and installer (no resident probe/demo)" >&2; exit 1; }
  PACK_CFG="$PACK_CFG --cfg k6_channel_page"
fi
if [ "${K6_SYSTEM_DEMO:-0}" = 1 ]; then
  [ "$K6_OTA_SERVICE" = 0 ] && [ "$K6_SOTTO_SERVICE" = 0 ] || { echo "System demo requires cable-only non-voice host" >&2; exit 1; }
  PACK_CFG="$PACK_CFG --cfg k6_system_demo"
fi
if [ "${K6_SYSTEM_INSTALL:-0}" = 1 ]; then
  [ "$K6_OTA_SERVICE" = 0 ] && [ "$K6_SOTTO_SERVICE" = 0 ] || exit 1
  PACK_CFG="$PACK_CFG --cfg k6_system_install"
fi
case "${K6_RAM0:-0}" in
  0) ;;
  1) PACK_CFG="$PACK_CFG --cfg k6_ram0" ;;
  small) PACK_CFG="$PACK_CFG --cfg k6_ram0 --cfg k6_ram0_small" ;;
  stack) PACK_CFG="$PACK_CFG --cfg k6_ram0 --cfg k6_ram0_stack" ;;
  *) echo "invalid K6_RAM0" >&2; exit 1 ;;
esac
if [ "${K6_CONFIG_FSK:-0}" = 1 ]; then
  [ "${K6_FSK_RX_SERVICE:-0}" = 1 ] && [ "${K6_LEAN_TXDOG:-0}" = 1 ] || { echo "Config FSK requires Data and watchdog" >&2; exit 1; }
  PACK_CFG="$PACK_CFG --cfg k6_config_fsk"
fi
if [ "${K6_EL_POWER:-0}" = 1 ]; then PACK_CFG="$PACK_CFG --cfg k6_el_power"; fi
if [ "${K6_LEAN_TXDOG:-0}" = 1 ]; then PACK_CFG="$PACK_CFG --cfg k6_lean_txdog"; fi
if [ "${K6_HEALTH_TELEMETRY:-0}" = 1 ]; then PACK_CFG="$PACK_CFG --cfg k6_health_telemetry"; fi
if [ "${K6_NATIVE_FM:-1}" = 0 ]; then PACK_CFG="$PACK_CFG --cfg k6_no_native_fm"; fi
if [ "${K6_FONT_SUBSET:-0}" = 1 ]; then PACK_CFG="$PACK_CFG --cfg k6_font_subset"; fi
if [ "${K6_RASTER_BOOT:-0}" = 1 ]; then PACK_CFG="$PACK_CFG --cfg k6_raster_boot"; fi
if [ "${K6_SERIAL_TRANSPORT:-0}" = 1 ]; then
  PACK_CFG="$PACK_CFG --cfg k6_serial_transport"
fi
if [ "${K6_SETTINGS_PAGE:-0}" = 1 ]; then
  [ "${K6_CHANNEL_PAGE:-0}" = 1 ] || { echo "Settings page requires channel page loader" >&2; exit 1; }
  PACK_CFG="$PACK_CFG --cfg k6_settings_page"
fi
if [ "${K6_IQ_PAGE:-0}" = 1 ]; then
  [ "${K6_SERIAL_IQ:-0}" = 1 ] && [ "${K6_RAM0:-0}" = 1 ] && [ "${K6_SYSTEM_INSTALL:-0}" = 1 ] || { echo "IQ page requires expanded arena and system installer" >&2; exit 1; }
  python3 "$HERE/apply-iq-page.py"
  PACK_CFG="$PACK_CFG --cfg k6_iq_page"
fi
if [ "${K6_SERIAL_IQ:-0}" = 1 ]; then
  [ "$K6_EXPLORE_HOST" = 1 ] || { echo "Serial IQ requires Base host" >&2; exit 1; }
  PACK_CFG="$PACK_CFG --cfg k6_serial_iq"
fi
if [ "${K6_SERIAL_LAB:-0}" = 1 ]; then
  PACK_CFG="$PACK_CFG --cfg k6_serial_lab"
fi
if [ "${K6_VFO_PROBE:-0}" = 1 ] || [ "${K6_VFO_SPI_PROBE:-0}" = 1 ]; then
  [ "${K6_FSK_RX_SERVICE:-0}" = 1 ] || { echo "VFO probe requires Data" >&2; exit 1; }
  PACK_CFG="$PACK_CFG --cfg k6_vfo_probe"
fi
if [ "${K6_VFO_SPI_PROBE:-0}" = 1 ]; then
  PACK_CFG="$PACK_CFG --cfg k6_vfo_spi_probe"
fi
if [ "${K6_VFO_SELECTOR:-0}" = 1 ]; then
  [ "${K6_VFO_SPI_PROBE:-0}" = 1 ] || { echo "VFO selector requires SPI probe" >&2; exit 1; }
  PACK_CFG="$PACK_CFG --cfg k6_vfo_selector"
fi
if [ "${K6_SOTTO_VFO:-0}" = 1 ]; then
  [ "${K6_VFO_SELECTOR:-0}" = 1 ] || { echo "SOTTO VFO mode requires the VFO selector (K6_VFO_SELECTOR=1)" >&2; exit 1; }
  python3 "$HERE/apply-sotto-installed-vfo-mode.py"
  PACK_CFG="$PACK_CFG --cfg k6_sotto_vfo"
fi
if [ "${K6_SOTTO_RFTEST:-0}" = 1 ]; then
  [ "${K6_SOTTO_VFO:-0}" = 1 ] || { echo "SOTTO RFTEST requires SOTTO VFO mode (K6_SOTTO_VFO=1)" >&2; exit 1; }
  PACK_CFG="$PACK_CFG --cfg k6_sotto_rftest"
fi
if [ "${K6_SCHEDULED_PROBE:-0}" = 1 ]; then
  [ "${K6_VFO_SELECTOR:-0}" = 1 ] || { echo "Scheduled probe requires selector" >&2; exit 1; }
  PACK_CFG="$PACK_CFG --cfg k6_scheduled_probe"
fi
if [ "$K6_OTA_SERVICE" = 0 ]; then
  PACK_CFG="$PACK_CFG --cfg k6_no_ota"
fi
if [ "${K6_DESIGNER_SERVICE:-0}" = 1 ]; then
  PACK_CFG="$PACK_CFG --cfg k6_designer"
fi
if [ "${K6_FSK_RX_SERVICE:-0}" = 0 ]; then
  PACK_CFG="$PACK_CFG --cfg k6_no_fsk_rx"
fi
if [ "${K6_SCAN_SERVICE:-1}" = 0 ]; then
  PACK_CFG="$PACK_CFG --cfg k6_no_scan"
fi
if [ "$K6_SOTTO_SERVICE" = 0 ]; then
  PACK_CFG="$PACK_CFG --cfg k6_no_sotto"
fi
if [ -n "$PACK_CFG" ]; then
  (cd "$WORK" && K6_AURA_FEATURES="$FEATURES" K6_SCAN_SERVICE="${K6_SCAN_SERVICE:-1}" K6_FSK_RX_SERVICE="${K6_FSK_RX_SERVICE:-0}" RUSTFLAGS="${RUSTFLAGS:-} -C link-arg=-Tlink.x$PACK_CFG" "$K6_CARGO" build --release --features "$FEATURES")
else
  (cd "$WORK" && K6_AURA_FEATURES="$FEATURES" K6_SCAN_SERVICE="${K6_SCAN_SERVICE:-1}" K6_FSK_RX_SERVICE="${K6_FSK_RX_SERVICE:-0}" "$K6_CARGO" build --release --features "$FEATURES")
fi
ELF="${CARGO_TARGET_DIR:-$WORK/target}/thumbv6m-none-eabi/release/aura"
llvm-size -A "$ELF" > "$OUT/size.txt"
FLASH=$(awk '$1==".vector_table" || $1==".text" || $1==".rodata" || $1==".gnu.sgstubs" || $1==".k6_id" || $1==".data" {n += $2} END {print n+0}' "$OUT/size.txt")
for pass in 1 2 3; do
  K6_FLASH_USED_BYTES="$FLASH" K6_AURA_FEATURES="$FEATURES" K6_SCAN_SERVICE="${K6_SCAN_SERVICE:-1}" K6_FSK_RX_SERVICE="${K6_FSK_RX_SERVICE:-0}" python3 "$HERE/apply-flux-branding.py"
  python3 "$HERE/apply-theme-splash.py"
K6_FLUX_ART_DIR="$K6_FLUX_ART_DIR" python3 "$HERE/apply-raster-boot.py"
  if [ -n "$PACK_CFG" ]; then
    (cd "$WORK" && K6_AURA_FEATURES="$FEATURES" K6_SCAN_SERVICE="${K6_SCAN_SERVICE:-1}" K6_FSK_RX_SERVICE="${K6_FSK_RX_SERVICE:-0}" RUSTFLAGS="${RUSTFLAGS:-} -C link-arg=-Tlink.x$PACK_CFG" "$K6_CARGO" build --release --features "$FEATURES")
  else
    (cd "$WORK" && K6_AURA_FEATURES="$FEATURES" K6_SCAN_SERVICE="${K6_SCAN_SERVICE:-1}" K6_FSK_RX_SERVICE="${K6_FSK_RX_SERVICE:-0}" "$K6_CARGO" build --release --features "$FEATURES")
  fi
  llvm-size -A "$ELF" > "$OUT/size.txt"
  NEXT_FLASH=$(awk '$1==".vector_table" || $1==".text" || $1==".rodata" || $1==".gnu.sgstubs" || $1==".k6_id" || $1==".data" {n += $2} END {print n+0}' "$OUT/size.txt")
  if [ "$NEXT_FLASH" -eq "$FLASH" ]; then
    FLASH="$NEXT_FLASH"
    break
  fi
  FLASH="$NEXT_FLASH"
  if [ "$pass" -eq 3 ]; then
    echo "Flux About flash-size metadata did not stabilize after 3 relinks" >&2
    exit 1
  fi
done
if [ "$FLASH" -gt 122880 ]; then
  echo "image needs $FLASH bytes; KD32F328 application limit is 122880 bytes" >&2
  exit 1
fi
if [ "${K6_RAM0:-0}" != 0 ]; then
  python3 "$ROOT/tools/k6_ram0_audit.py" "$ELF" --mode "$K6_RAM0" > "$OUT/ram0-layout.json"
fi
llvm-objcopy -O binary "$ELF" "$OUT/$IMAGE.bin"
if [ "${K6_CHANNEL_PAGE:-0}" = 1 ]; then
  python3 "$ROOT/tools/k6_channel_page.py" "$OUT/$IMAGE.bin" "$ELF" "$OUT/channel-system.bin"
fi
if [ "${K6_SETTINGS_PAGE:-0}" = 1 ]; then
  python3 "$ROOT/tools/k6_settings_page.py" "$OUT/$IMAGE.bin" "$ELF" "$OUT/settings-system.bin"
fi
if [ "${K6_IQ_PAGE:-0}" = 1 ]; then
  python3 "$ROOT/tools/k6_iq_page.py" "$OUT/$IMAGE.bin" "$ELF" "$OUT/iq-system.bin"
fi
python3 - "$OUT/$IMAGE.bin" <<'PY'
import hashlib
from pathlib import Path
import sys
image = Path(sys.argv[1])
checksum = f"{hashlib.sha256(image.read_bytes()).hexdigest()}  {image}\n"
image.with_suffix(image.suffix + '.sha256').write_text(checksum)
print(checksum, end='')
PY
if [ "$K6_OTA_SERVICE" = 0 ]; then
  echo "Built cable-only FD6818B image with features: $FEATURES"
elif [ -n "${K6_MOD_KEY_FILE:-}" ]; then
  echo "Built keyed FD6818B image with features: $FEATURES"
else
  echo "Built unkeyed FD6818B image with features: $FEATURES; it refuses every module."
fi
