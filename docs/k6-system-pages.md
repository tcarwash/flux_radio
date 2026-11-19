# Flux system overlays: staged design

Ten received PXK6 cards retain their existing first ten 4 KiB sectors:
0x190000..0x19A000 (exclusive end). The 8 KiB alignment gap remains reserved,
not writable by the card API. The new system bank is 0x19C000..0x200000:
25 fixed 16 KiB slots. The existing 32-slot library stays unchanged, including
legacy pages 9–12 and help decks 13–15. Channels, settings and calibration
are outside the new bank. Older cards beyond slot 9 must be exported before
migration; the first ten physical slots are retained, not necessarily the ten
newest cards.

## Lifecycle

Every firmware flash via the bundle installer must erase the entire system bank,
then install and read back the exact pages for the new firmware build. Do not
allow ordinary user modules, the card API or generic raw-write commands to erase
or program this bank. A firmware-only flash without its bundle must leave the
system features unavailable, never execute stale pages. Missing, damaged or
wrong-build pages fail closed and leave the core cable recovery path usable.
The opt-in experimental installer and channel page now exist. Bundle integration
is still pending; no physical radio validation or migration has been performed.

## Runtime design

Reuse upstream kp.h/kp.ld, kl_page validation and build-specific function tables.
Page execution shares the arena with user modules and resident callbacks. No
nested page changes, no retained pointers into an evicted page, no use of the
arena as a callback stack while a page owns it. Keep persistent state in the
core (or explicitly allocated arena leases), flash driver and recovery in MCU
flash. Use the exact firmware identity and expected page ID/address/table size;
validate image and entry bounds before calls. No arbitrary native Rust ABI
across independently linked binaries: use explicit C ABI shims.

Start with a small receive-only proof-of-concept and emulate missing, corrupt,
wrong-build and replaced pages. Then move channel editor / FM / scan features.
Integrate a privileged cable install transaction after verifying firmware build
identity, and test reinstall including interruption and recovery. Ordinary user
library slots remain untouched. The existing full-bank card API on old firmware
must not be used with new system contents; a later downgrade must wipe the
system bank or refuse the downgrade. No production activation until physical
validation and safe installation support are in place.

## Experimental checkpoint (2026-11-19)

`K6_CHANNEL_PAGE=1 K6_SYSTEM_INSTALL=1 K6_RAM0=1` with Base, Scan, Data
(including serial IQ), Designer, native FM disabled, no Sotto/OTA/resident probe.
Core image 122720 bytes of 122880 (160 free). Slot 0: K6S envelope, page ID 18,
two C ABI entry points, 1596 bytes. The page formats channel values and adjusts
scalar fields; native list UI, digit/name editing, save/delete and storage remain.
First 8192 arena bytes reserved for page loading, upper 6784 for host call stack;
reserved main stack 3144 bytes. Page is freshly validated on each load, not cached.

Evidence: 19 targeted host/validator tests passed. Actual Cortex-M0 simulator CPS
installation passed wrong-build rejection, sequential erase/retry, exclusive mode,
interruption after first sector, restart, whole-bank readback and byte-for-byte
preservation outside the system bank. Interactive channel editing in the simulated
radio, physical stack high-water measurements, hardware recovery and production
bundle installation remain unverified. No activation record for multi-page
transactions yet; the current installer supports only slot 0. CRC is integrity,
not authorization against malicious binaries. Not a release firmware.

Next: verify interactive execution and save/cancel/delete; generalize loader and
installer to multiple registered pages with a final activation record; migrate
remaining channel UI, then settings UI and FM/scan code based on measured size.
Do not disable IQ to claim flash savings. Preserve core radio/storage and recovery.

## Packed bank experiment (after 188e097)

KSD1 directory at 0x19c000; first 12 bytes magic and 8-byte build ID.
Entry at base+16+8*ID: absolute SPI address u32 and envelope length u32,
little endian. IDs 1..255, four-byte aligned payload from 0x19d000.
Every page has its own K6S integrity tag and exact build identity. Installer
0x80..0x85 erases directory first, stages ascending IDs, commits payload and
entry, then writes build ID and magic last. No fixed 16 KiB system slots.
Full IQ build 123200 bytes (320 over limit). Separate no-IQ diagnostic full-radio
emulator passed two-page installation, activation and whole-bank comparison.

## User library prototype (KUL2, not enabled in firmware)

Two 4 KiB directory sectors at 0x110000, 0x111000; 126 payload sectors from
0x112000. Each directory uses 1036 bytes: magic, generation, CRC32 of bytes
12 onward, inverted generation, then 255 four-byte logical ID entries
(start sector, count, length LE16, zero length means empty). Allocation is
first-fit contiguous sectors; copy-on-write replacement and directory commit
last. Old allocations may be reused following successful commit; fallback to
an older directory after later unrelated media corruption can reference stale
payload, so callers must still validate object tags. No implicit reformat.
`c/k6/user_store.c` is an independent allocator, not wired into kl_* or theme
paths. `tools/k6_user_store_migrate.py` builds an offline image from a backup,
never writes to a radio. Preserve legacy IDs; theme slots are conservatively
copied at full size. Unknown nonempty slots refuse migration. Current objects
are limited to 65535 bytes in this format; executable arena limits still apply.
Needed: checked integration at all existing direct slot-address users, capability
negotiation, migration recovery, storage-aware host UI, defrag and physical test.

### Packed storage checkpoint

KUL2 user objects retain logical IDs, with alternating CRC-protected directories and
copy-on-write payload updates. Catalog flash operations save original and compacted
banks plus a pending recovery journal before writing. Recovery currently requires a
running cable session; bootloader-only recovery and exhaustive power-cut testing
are not release-qualified. Keyed OTA firmware builds with the resident channel UI;
system overlays remain cable-only. Sotto compiles but exceeds the application budget.
The full IQ-enabled four-pack image is 125328 bytes, over the 122880-byte limit.
Do not ship this checkpoint as a production image. The simulator uses the verified
IQ-disabled restoration build. Firmware-only packed upgrades still restore storage.

### Combined IQ and Memories development checkpoint

IQ belongs to Data; its opt-in `K6_SERIAL_IQ=1 K6_IQ_PAGE=1` build requires expanded RAM0 and the cable-only system installer. Page 19 occupies the arena tail after 11000 bytes of DMA scratch; the separately linked page measured 2492 bytes with 1484 bytes spare. The resident UART TX ISR remains outside the overlay. Combined Base/Scan/Designer/Data with memory protocol and IQ overlay linked at 123504 bytes (624 over). Catalog defaults keep experimental IQ disabled explicitly until a qualified image fits; do not interpret that as a fully featured Data release. Base memory protocol compiled and passed a real emulator NOAA merge/readback. Host recovery fault model passed; physical RF and combined-image runtime remain unqualified. Packed first-time bootloader installation currently fails closed without a running backup session; starter provisioning needs a dedicated migration path before release.
