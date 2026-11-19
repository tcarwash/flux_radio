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
The initial system bank is reserved only: erase/write/execute support is not
implemented yet. No migration or hardware writes have been performed.

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
