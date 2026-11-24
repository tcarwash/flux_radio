# CW host TX startup: PA-on transient and stale TX band gain audit

**2026-11-24 · independent CW/non-Sotto work · source/host simulation only.**

This audits the *actual generated, pinned upstream Aura source* for its `Radio::enter_tx()`, `app::pager::mod_tx_on()` and `mod_tx_pa()` call paths, plus the production `cwkey.c` keyer. It never starts an SDR, connects to radio hardware, writes RF registers or keys an RF transmitter. A physically clean CW carrier or physical PA leakage is **not** demonstrated.

## Findings

**1. PA is enabled during `tx_on()`, before CW can request PA-off.** In the generated FD6818B radio implementation, `Radio::enter_tx()` performs `fd6818.tx_on()`, then `fd6818.pa_enable()`, then the TX GPIO band route, and returns. The module host subsequently writes `MOD_TX_STATE` (`REG30=0xC1FA`), marks the module keyed, logs the startup and returns. Only then does `cwkey.c` call `tx_pa(0)` to perform `cw_carrier_off()` → `fd6818.pa_off()` (`REG36=0x0022`). The independent Antigravity source review (`d9339330a8c8`, Gemini 4 Hydrogen Medium, read-only) corroborated that order.

The **software sequence** is certain in the checked source, but it proves neither transmitted RF during the interval nor its level, bandwidth or duration. `REG36=0x0022` clearing `PACTL` does not itself prove zero exciter/carrier leakage on this B-chip. The existing CW keyer's 300-ms PLL settle interval starts only **after** the host has transiently configured PA on, not before. There may also be interspersed CPU, watchdog and logging delays that cannot be inferred from counting SPI operations.

**2. PA gain can use the previous band.** `fd6818.pa_enable()` chooses `PA_GAIN_UHF` (`0x00A2`) or `PA_GAIN_VHF` (`0x0088`) from its cached `band_uhf` flag. Yet `enter_tx()` invokes `pa_enable()` *before* `set_tx_band_uhf/vhf()`, the method that updates the flag and programs GPIO2/GPIO3. A host Rust shim compiling the **real extracted methods** confirms the resulting case: with a stale VHF state, the original `enter_tx()` for UHF logs `tx_on,pa_vhf,band_uhf`; likewise a later VHF CW start gets `pa_uhf` before `band_vhf`. This is a source-level ordering defect and is independent of whether the radio actually radiates a startup pulse.

## Read-only source audit and conditional timing model

`tools/k6_cw_host_startup_audit.py` checks that the generated Rust host, FD6818B driver and current C module still have the known API3 call order. The tool **fails closed on source drift**. It reports a *hypothetical bus-only* span using a specified fixed time per SPI transaction, not the real duration of radio transmission. Example on an already generated Aura checkout (the example is read-only):

```sh
R=firmware/k6-aura/build/aura-fd6818b-port/src
python tools/k6_cw_host_startup_audit.py \
  --radio "$R/device/radio.rs" \
  --pager "$R/app/pager.rs" \
  --driver "$R/drivers/fd6818b.rs" \
  --spi-us 117 --json /tmp/cw-host-startup.json
```

The source-verified UHF host path has **eight modeled 3-wire transactions** after the initial PA-enable completion through the first module PA-off completion: four for the two read/modify/write band-GPIO calls, two for the TX LED GPIO call, one for the `MOD_TX_STATE` write, and one for the module PA-off write. This yields **936 µs at an assumed 117 µs/transaction**, of which four transactions or **468 µs** follow the new band's route assertion. **These values are neither minimum nor maximum physical RF-on time**; GPIO read/write timing, other program execution, previous band state, analog response and module logging are not captured. Different fast/slow SPI assumptions only rescale these conditional model figures.

## Experimental source-only fix: opt-in and not active

`firmware/k6-aura/apply-cw-pa-band-order.py` is an **explicitly opt-in** patch for a separate generated Aura `device/radio.rs` file. It relocates the existing `pa_enable()` statement **after** `set_tx_band_*()` in exactly two methods, `enter_tx()` and `enter_tx_keyed()`, preserving the PA command, power, limits, band-lock, watchdog and module API3 semantics. It makes no other source changes, and is idempotent. It refuses unknown or restructured code, a missing VHF/UHF arm, or duplicate PA calls. Critically, it is **not called by** the normal `build-fd6818b.sh` and is not an active firmware behavior change.

To inspect the candidate without writing anything:

```sh
python firmware/k6-aura/apply-cw-pa-band-order.py \
  --radio "$R/device/radio.rs" --check

# Only in a separate *disposable* generated checkout, never in an active
# build or radio without separately reviewed integration/qualification:
# python firmware/k6-aura/apply-cw-pa-band-order.py \
#   --radio "$R/device/radio.rs" --apply
```

That order change fixes the band-gain mismatch in the mocked host but **does not** remove the initial PA-on window; after band routing, PA would still be enabled before the host finishes LED/state/logging and the CW module calls `tx_pa(0)`. Its eventual activation should be tested against the full firmware build and normal FM transmit across both bands, and coordinated with the Sotto agent if their build uses `Radio::enter_tx()`.

A future genuinely **quiet-start** operation needs a distinct, opt-in **fail-closed** transmitter interface and a fully PA-off startup sequence (including `REG30` state, PLL, driver and band routing). The existing API3 `tx_on` is used by legacy packet, APRS, FT8 and tone modules that expect PA on at return; changing it globally is unsafe for compatibility. Overloading `max_ms` with a flag is unsafe because older hosts clamp high values to 120 seconds and would **enable** PA instead of refusing. Do not allocate a new API level without coordinating with the Sotto integration owner, whose API31 work is active; this research change adds **no API slot** and does **not** enable the candidate by default.

## Tests and scope

```sh
python -m pytest -q tests/test_k6_cw_host_startup_audit.py \
  tests/test_k6_cw_pa_band_order_patch.py

# Optional stronger test against an already-generated pinned upstream checkout:
K6_CW_AURA_SOURCE_ROOT="$R" python -m pytest -q \
  tests/test_k6_cw_host_startup_audit.py tests/test_k6_cw_pa_band_order_patch.py
```

The optional test extracts the actual generated Aura Rust `enter_tx()` and `enter_tx_keyed()` method bodies, compiles them into an ordinary **host Rust mock shim**, and compares PA gain selection and band routing before/after the source-only patch. It does not compile or flash the complete Cortex-M0 firmware. Both normal and alternate generated-source paths are verified without writing the real checkout. CI tests also exercise failure on unsupported input/signature drift, read-only operation, explicit `--apply` idempotence and unchanged default build configuration.

Next physical gate is [CW TX-35, issue #38](https://github.com/tcarwash/flux_radio/issues/38): under a separately approved exclusive bench lease, record conducted complex SDR IQ around the first TX entry, after RF route assertion, first `tx_pa(0)`, held-PLL lead-in, first `tx_pa(1)` and final key-up. Match the measured RF amplitude/phase with real register/PA signal timing. The source order alone cannot qualify clean RF startup or carrier-off isolation. Until then, **no experimental PA or quiet-start firmware is enabled** and no hardware was accessed by this work.
