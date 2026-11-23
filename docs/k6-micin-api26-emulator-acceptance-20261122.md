# Flux TX-12C — MICIN API26 isolated firmware / emulator acceptance

Date: 2026-11-22 local / 2026-11-23 UTC.
Branch: `research/native-fsk-pdm`.
Status: **compiled + emulator acceptance only; not flashed or transmitted**.

## Outcome

The opt-in **FD6818B MICIN-TX API26** research firmware was rebuilt from
a clean pinned Aura source in a detached Git worktree. The resulting
99-KiB image passes its compiled-image live-control emulator suite:

**8 passed, 1 skipped**. The sole skip is the packed-library-mutation
test, because this image was built without the optional user-store /
packed SPI library feature. The more relevant **cooperative-cancellation
test passed** while using the firmware's legacy fixed-slot SPI format.

This is a **software test-harness correction**, not a firmware RF
modulation change. It does not prove physical TIM14 watchdog operation,
MICIN input safety, audio-to-RF transfer, RF output spectrum, or a
properly isolated external stimulus.

## Exact experimental build

- Parent Flux source snapshot: `55de4bf` (subsequent committed
  test-harness patch has no firmware code changes).
- Pinned upstream Aura revision:
  `c3aeb17aec0fd09dccf931b52f6c7d5dbf72e7ac`.
- Pinned `sotto` submodule revision:
  `7715cecc0e6b3f2fbacdd17260026e12a952037a`.
- Cargo features:
  `blowfang-host,fd6818b,lean,spilib,txdog-quiet`.
- Build environment:
  `K6_LIVE_SERIAL=1`,
  `K6_LEAN_TXDOG=1`,
  `K6_RAM0=stack`,
  `K6_SERIAL_TRANSPORT=1`,
  `K6_SCAN_SERVICE=0`,
  `K6_MICIN_TX=1`, OTA disabled,
  `K6_USER_STORE` not set (disabled).

Research image metadata:

| Field | Value |
|---|---|
| Image | `flux-fd6818b-research.bin` |
| Bytes | 100,688 |
| SHA-256 | `be95002a7dbe8760df9d3affcee9442564285ad215936051033fb22612b5ebe1` |
| Module ABI | API level 26 from simulated capability response |
| Test result | 8 passed, 1 skipped |
| Hardware TX/flash | None |

Archived, **NOT release approved** image and diagnostic artifacts:

`~/flux-bench/tx-injection-20261122/micin-api26-rebuild/`

The archive includes `acceptance.json`, `size.txt`,
`ram0-layout.json`, compressed build output, final emulator log
and one-pass cooperative-cancellation test log. The image is
**not** staged for upload or flashing.

## The two apparent failures were in the harness/build method

### 1. Cached generated-source rebuild is not idempotent

The initial attempt reran
`firmware/k6-aura/build-fd6818b.sh` against the previously
patched local `build/aura-fd6818b-port` checkout. It failed
with `cannot locate module API level 16/17 initializer`
because the source already contained a later API26 initializer.

This is a limitation of the present generated-overlay build
process, not evidence that the MICIN firmware cannot compile.
A fresh, detached Flux worktree prevents modifying the existing
generated Aura checkout. The new worktree also requires
`git submodule update --init sotto`; without it the C codec
builder fails on the `c/fixed` symlink.

The fresh build succeeded without networking by using the
previously available **pinned Aura repository as a local Git
clone source**. Do not use `git reset --hard` on another
agent's generated source as an implicit workaround.

### 2. Emulator test assumed *exactly* API25 and always packed SPI

`tests/test_k6_live_control_sim.py::test_compiled_live_coop_cancel_token`
previously asserted `module_api_max==25`. This experimental
MICIN image advertises **26**, so the assertion failed before
testing cancellation.

Changing it to `>=25` exposed the second, real test harness
error: the test always populated a **packed** SPI module store via
`k6_user_store_migrate.compact`, even though the built firmware
did not advertise packed-library mutation. The cooperative RUN
request then returned `Unsupported command, address or frequency`
because that slot did not exist in the expected **fixed**
16-KiB module-slot layout.

The corrected regression detects `lib_mutate` in the firmware's
reported features, choosing either:

- `compact({26: envelope})` in the packed store; or
- `LIB_BASE + 26 * LIB_SLOT` in the fixed-slot SPI store.

The same test still exercises a nonzero authentication token,
injects an invalid cancellation token, validates eventual
authenticated cancellation, checks module return status and
reports, checks RX idle and VFO restoration, and requires SPI
contents to be unmodified by module execution.

With the fix, the targeted test passed and a subsequent **full
compiled-image suite** passed 8 tests, skipping only packed-library
mutation (which is not part of this build configuration).

## Reproduce from a fresh isolated worktree

This is a **build-and-test-only** recipe, never a flashing recipe.
Select a **new path**; do not run the overlay build on an already
mutated Aura generated source without arranging a clean checkout.

```sh
cd /storage5/dev/flux-native-fsk-pdm
git worktree add --detach /storage5/dev/flux-micin-rebuild-check HEAD
cd /storage5/dev/flux-micin-rebuild-check
git submodule update --init sotto

AURA_REPO=/storage5/dev/flux-native-fsk-pdm/firmware/k6-aura/build/aura-fd6818b-port \
K6_LIVE_SERIAL=1 \
K6_LEAN_TXDOG=1 \
K6_RAM0=stack \
K6_SERIAL_TRANSPORT=1 \
K6_SCAN_SERVICE=0 \
K6_MICIN_TX=1 \
K6_OUT=/storage5/dev/flux-micin-rebuild-check/firmware/k6-aura/out-micin \
sh firmware/k6-aura/build-fd6818b.sh

# Requires Unicorn >=2.1 available in Python's import path.
# This host has previously used a separately installed dependency:
PYTHONPATH=/tmp/flux-fsklab-unicorn-deps \
K6_SIM_LIVE_IMAGE=/storage5/dev/flux-micin-rebuild-check/firmware/k6-aura/out-micin/flux-fd6818b.bin \
python3 -m pytest -q tests/test_k6_live_control_sim.py -rs
```

An equivalent fresh Git checkout must have access to the pinned Aura
source, plus the checked-out `sotto` submodule. The
`/tmp/flux-fsklab-unicorn-deps` Python package directory is
ephemeral and may require recreation later.

The current isolated source/build tree remains at
`/storage5/dev/flux-micin-rebuild-source-20261122`; the durable
artifact copy is in the `flux-bench` archive above.

## Remaining actual hardware gates

- Verify actual microphone jack wiring, bias, safe input amplitude,
  source impedance, isolation and DC/AC coupling.
- Establish provable audio-loop continuity and RF-gate timing before
  assuming a whole 44-ms period appears inside the 100-ms keyed
  window ([TX-12B](k6-micin-loopable-coupling-20261122.md)).
- Confirm radio-side independent watchdog and recovery in the
  appropriate conducted dummy-load environment. Emulator code
  coverage alone is not independent hardware timeout proof.
- Validate actual TX filter bypass, coherent RF frequency response
  for balanced **and** long-plateau voltage stimuli, startup
  artifacts and emissions under attenuated SDR reception.
- Never equate analog FM modulation with a general RF I/Q
  transmitter or with independent RF amplitude control.

Related: [MICIN bypass state and register path](k6-micin-bypass-lab.md),
[all TX routes](k6-tx-next-experiments.md).
