# Sotto as a loadable Flux modem

Decision (2026-11-23): keep the Sotto modem in a user-installable catalog
module. The optional Sotto firmware profile is a development host and decoder
service, not a commitment to resident modem-specific RF logic. Do not consume
base firmware flash with a Sotto-only transmitter or auto-install a modem.

## First module: staged Sotto-1200 TX

User workflow: launch SOTTO from Catalog, hold PTT to record, release PTT to
transmit a bounded recorded utterance. The microphone capture phase must keep
the PA off; stop its sampler, restore microphone routing, and verify
codec/capture/SPI success before allowing transmission. A cancel, power-off,
lost PCM block, missing codebook, watchdog expiration or RF fault must abort.
No automatic retries or automatic TX on module load. Longer streaming comes
after the staged path is independently qualified.

### Prepared module-side state

`c/k6/mod/sotto_tx_stage.h` implements the bounded record/seal/send controller.
It stores up to 80 six-byte Sotto-1200 superframes (3.2 seconds, 480 bytes)
plus one guarded FIFO packet and control state (at most 576 bytes total).
Unlike the two-slot streaming packet queue, it retains the complete recording
until capture ends. The module code can use this in caller-owned scratch.

- Only a clean, nonempty sealed recording produces packets.
- BEGIN, successive 320 ms voice segments (last segment may be shorter),
  and END use the FDV1 control framing, all 66-byte guarded frames.
- Advance on confirmed physical completion only; failure freezes the session.
- Refuse a message-ID epoch that cannot accommodate the entire bounded capture.
- A caller must stop recording at capacity, flush the encoder, and abort on
  any lost samples. The state machine cannot validate real microphone/PA/SPI
  operation itself.

`tests/test_k6_sotto_tx_stage.py` validates exact segment lengths, CRC and
guard bytes, recording loss/overflow, failure without retries, and runs
the compiled Cortex-M0 instructions in Unicorn (26 synthetic payload
superframes, six guarded packets). **13 tests passed, including actual ARM
emulation**. No codec samples are captured or transmitted by these tests.
This is reusable module code, not an installable or RF-qualified modem yet.

## RAM and integration gates

The current Sotto host's module arena is 11,776 bytes. The earlier
`research/live-dv-guarded-segment-scheduler-20261123` combined target-code
probe required 8,120 bytes code/RO + 3,148 bytes codec state + 848 bytes
stack high-water + 640 bytes candidate PCM ring + 204 bytes guarded queue:
12,960 bytes before envelope, driver, and unmeasured overhead. Replacing the
204-byte queue by the bounded staged record state (up to 576 bytes) raises the
conservative modeled minimum to 13,332 bytes. A 15,408-byte arena leaves
2,076 bytes optimistic headroom before additional module driver, metadata,
scratch alignment, IRQ, SPI context, and safety needs. These are modeled
numbers, not an actual complete position-independent module measurement.
Reuse the common arena-expansion effort, not a Sotto-specific fixed address.
The target must prove its actual arena and scratch capacity at run time.

### Actual relocatable encoder qualification (2026-11-24)

`tools/k6_sotto_module.py --out NEW_DIRECTORY` builds the `SOTTO TEST`
module binary plus the deterministic public `sotto-ivf.k6sc` search-table deck.
The module uses API 5 read-only SPI and the existing fixed-slot library format.
It validates the pinned codebook header and complete codebook and IVF deck CRCs.
The generated temporary encoder source adds an explicit per-instance SPI
context to the upstream shape-search call chain; it does not modify the Sotto
submodule or depend on reserved interrupt vectors or mutable statics. The Sotto
pin advances to `598f1bce87c22ebada2b9d0d1fcfd89107b33a09`, the exact slim
encoder revision used in the research branch.

The final binary is 9,271 bytes, including the relocatable stack trampoline;
its two-address link check passes. The 4,182-byte search deck is separate from
module code. It is a data artifact, not an executable module; installation
must eventually use the verified deck/packed-library workflow. The current
bundle is for offline development and must not be published as a working modem.

Tests execute the actual catalog-format binary at two addresses in Cortex-M0
Unicorn with a read-only mock SPI API. The unmodified upstream native encoder
is an independent reference for all 26 six-byte superframes from 104 synthetic
10 ms blocks. All encoded bytes match, as does the digest of the six FDV1
packets (`a1471c22`, FNV-1a). Fault cases reject a damaged codebook, damaged deck,
read failure during encoding, or the current smaller arena. The emulator
forbids all TX, radio register, and flash-write calls.

The usual Flux dedicated module stack is 1,152 bytes, below the measured
encoder peak. The qualification module therefore uses a 2,048-byte aligned
stack within caller-provided scratch with a 32-byte guard. Both relocations
preserve the original SP, use less than 256 bytes of host stack, retain the
scratch guard, and show 1,184 bytes of scratch-stack use. Hardware API/IRQ
stack overhead and interrupt behavior remain unqualified. The 15,408-byte
arena leaves at most 6,113 bytes of scratch before alignment and any telemetry;
with 3,152 bytes of encoder state, 560 bytes of record state, and 2,080 bytes
of reserved stack/guard, only about 320 bytes remain. The complete microphone
ring and RF driver do **not** yet fit in this conservative budget. Reduce
capture size, reuse phase-exclusive memory, optimize code, or expand the arena
before enabling real TX. The current 11,776-byte arena cannot run this module.

### Next implementation and bench order

1. Bring the previously qualified slim encoder/SPI deck and microphone
   capture frontend into a real relocatable catalog module; measure module
   image, scratch and stack, and fail closed on insufficient arena. Do not
   link the research probe's null/emulator-only flash context.
2. Use the proven 33-word FSK TX driver as the bounded post-recording
   consumer. Preserve host watchdog, VFO, PA, RFIC, sampler and interrupt
   state. No on-radio codebook/IVF writes while keyed.
3. Exercise actual ARM target code with valid Sotto frames and fault
   injection, then an independently reserved bench with dummy load and SDR.
   Verify recording length, zero lost PCM blocks, CRC-valid FDV1 controls,
   payload frames, termination and restoration. Account for the programming
   cable/microphone conflict in the operator workflow.
4. Only after this test, integrate native receive, jitter handling and
   playback, and extend from staged PTT to real-time voice.

The previous FSK-14 test established K6 mic-derived audio through native RF
with host encoding, not autonomous K6 mic encoding. The user's newer TX
success is acknowledged but its exact fixture is not independently
identified here. No hardware touched during this development checkpoint.

### Physical FDV1 qualification (2026-11-23, local evening)

On the owner's dummy-load bench, build `aba032cfc1d2b5e1` (Base + Data,
packed library, API 25) ran the separately compiled 2,344-byte API-5
`SOTTO RFTEST` module exactly once. Six guarded RF frames were received by
an independent RTL-SDR blind public-sync and CRC receiver; all 384 payload
bytes matched the frozen expectations (3,072 bits, zero errors). BEGIN,
four voice segments and END preserved all 26 Sotto codec frames; selected
offsets were 0/2/0/2/0/2 with zero sync errors. Firmware returned 6.
The source fixture is public synthetic encoder output, not live speech.
The temporary empty slot 30 was installed and verified using the legacy
loader (the host lacks live library mutation), executed over live serial,
and erased afterward. All 25 original packed object content hashes and
both VFO settings matched after cleanup. No firmware flash or retry.

- Frozen module SHA256 `64debca1a5d76adea2623e2cf6582bc60e32ea7e556504cafee9bc3c5a20d7be`
- Frozen fixture/scorer manifest SHA256 `1baedd278865568c2c171f82224cd961ce833d28d0c196c439583b09500b77b4`
- Packed test harness SHA256 `d4e5089c9b394fb6b32a41c583a4e28003805a721d87cb4aeab92ec78c84f4d1`
- Private IQ SHA256 `8e68591ce499e8cf9b66de7c63e9c16810cce7ff2ac6608589b41f6aae098371`
- [Bench result and release](https://github.com/tcarwash/flux_radio/issues/11#issuecomment-7122210068)
- 76 focused regression tests passed before the RF run. The RF fixture is
  inert without explicit compile-time bench gates.

This proves the raw-module staged RF path and FDV1 framing, not the new
resident Data-pack FSK API, real-time on-device encoding, or native RX.
The packed host reports a 14,976-byte arena; scratch excludes the host
stack reserve, envelope and loaded module. The earlier 15,408-byte model
is not an observed Flux allocation. The encoder workspace optimization
reduces measured stack high-water to 912 bytes with 9,375 bytes of code;
we still require a proven scratch/IRQ budget before a live encoder.

### Data host and live install integration

The Data pack owns the payload-agnostic native 4800-baud transmit service:
`fsk_configured(2, words, 33, 4800 | (21 << 16))`, with a checked arena
buffer, FD6818B identity, full preload, IRQ qualification, bounded timeout,
local cancellation, independent TX guard and PA-off completion. The caller
must supply framing, acquire a bounded TX lease and release it afterward.
The implementation must not restore TX register snapshots into RX after a
lost lease. `sotto_data_tx.h` consumes a sealed staged session with this API;
there is no implicit fallback, retry, or RF on installation. The previously
physically qualified raw RF fixture remains unchanged.

Development-only build command:

    sh firmware/k6-aura/build-sotto-data.sh /path/to/new-pack

This selects Base + Data, live serial, remote UI, packed storage and
`K6_RAM0=stack`, without IQ. The test image is 107,100 bytes, 15,780 bytes
below the application limit, build `638596533a67d6ec`, SHA256
`b79d85b40435dc51f2432c605e8a2d0ae5836328a5cb4c5d1bf042dc20a23ca9`.
It has the 11,776-byte arena and 6,616-byte main-stack reservation. API 25
is the current upstream-compatible Flux cancellation ABI ceiling on this
branch; live library mutation is a capability bit, not a new API number.
The 14,976-byte `K6_RAM0=1` layout used by the IQ-enabled catalog image does
not advertise mutation. The existing live install staging limit is 7,168
bytes including envelope. This profile is not yet a live encoder host:
the code itself is larger than that staging limit and the encoder needs
more scratch than either current layout can supply with its private stack.

Actual Cortex-M0 board emulation against the built firmware passed live
module install/readback/run/replace/remove including the 7168-byte staging
boundary. It also passed native FSK completion, absent IRQ timeout, stale
IRQ refusal, and wrong chip refusal. Hardware RF IRQs were modeled; this
is not a physical test of the new resident Data API.

The codebook/deck discovery path now supports KUL2 as well as fixed slots.
It validates both directory CRCs, complemented generations, sector counts,
range and overlap before choosing the newest valid directory; the deck
still requires exact format, length and full CRC. It recovers from one bad
copy, rejects two bad CRCs, CRC-valid overlapping extents and a damaged
deck. Actual relocatable encoder bytes are now 9,939 bytes, stack high-water
936 bytes in the generous synthetic algorithm harness, bit-exact digest
`a1471c22`. The test explicitly refuses the real arena sizes rather than
pretending that the synthetic scratch capacity is available on the radio.
39 focused module/stage tests passed. Remaining work: share/relocate codec
and table code or reduce workspace and qualify IRQ stack on target, hook up
PA11 capture and flush, extend the safe live install staging path for the
larger module, and verify the new Data service physically. Do not publish
this development profile as a finished Sotto voice modem.

The fifth full-board integration case also passed: live-installed API-24
Sotto module sends all six staged FDV1 packets using the common Data
API. The actual compiled Rust host preloaded all six 33-word buffers;
independent Python framing matched every byte including guard words.
The emulated host returns to RX and the module is removed over live serial.
No physical hardware access during these five Data API emulator cases.


### Balanced live-install host and reduced encoder stack (2026-11-23)

Integration branch `integration/sotto-memory` combines the native Data service
with the recovered catalog defaults and the installed-theme PTT fix. Normal
catalog builds use packed KUL2, live serial/install, remote LCD/keypad/PTT
and balanced RAM0. Serial IQ remains opt-in and retains its separate profile.

The measured build is 107,084 bytes (15,796 bytes below 120 KiB). RAM0 uses
3,560 bytes of its 3,840-byte allowance, the arena is 14,400 bytes and the
main stack reservation is 3,992 bytes. Live library mutation now advertises
a separate `lib_10k` capability (bit 21), increasing the staged envelope to
10,240 bytes while retaining a minimum 4,096-byte borrowed arena stack.
Clients still enforce 7,168 bytes on older lib_large firmware, and standalone
RAM uploads remain at 4,096 bytes. The compiled-board emulator passed the
full 10,240-byte replacement/readback/removal test, including bad tags and
protected slots. Observed main stack peak was 2,568 bytes (1,424 remaining);
borrowed arena runtime peak was 3,256 bytes, dedicated module peak 408 bytes.
These samples are not a worst-case interrupt or physical timing proof.

The encoder now stages input PCM and flush silence in the phase-exclusive
analysis workspace rather than reserving additional 80-sample stack arrays.
Actual relocatable Thumb code is 9,943 bytes, envelope 9,967 bytes. The
synthetic algorithm harness measured stack high-water of 648 bytes, down
from 936, with the same 26 codec frames, 6,261 SPI reads and FDV1 digest
a1471c22. Both relocation addresses and corrupt-table paths passed.
Retain the 2,048-byte scratch stack until full-host calls and interrupts
are measured. No claim that the complete microphone/voice module fits yet.

Remaining gaps:

* The normal lean host's fl_read callback refuses addresses below 0x190000.
  Sotto's codebook at 0x100000 and KUL2 deck at 0x110000 therefore cannot
  be read using that existing callback. Design a generic, typed,
  read-only resource lookup/read API with bounds and integrity validation,
  so modules need not duplicate the packed-directory parser. Do not
  extend write permissions or open arbitrary protected flash regions.
* At this size the balanced arena exposes only approximately 3,392 bytes
  of scratch after the envelope/alignment and 1,040-byte LCD callback
  reserve. The encoder state and staged recording alone need about 3,712
  bytes. Investigate an explicitly opted-in main-stack loan with a dynamic
  safety check, avoiding the second private module stack and redundant
  LCD callback reservation; preserve legacy module semantics. Also
  account for a real capture ring and additional driver code.
* A typed data-deck installation path and pinned codebook provisioning
  must be qualified separately. The 10 KiB live mutation capability is
  for ordinary executable modules, not arbitrary deck/codebook writes.
* Notification experiments are documented in
  research/flashlight-notifications.md. IRQ integration is still pending.
  The confirmed four-second, 20%-peak PB7 pattern uses no module arena or
  persistent SPI state in the intended core driver. The existing watchdog
  remap disables flash prefetch; a permanent SRAM mapping for the LED
  would slow the RF path, so do not enable it by default or allow it
  during timing-sensitive activity. No firmware flash performed.

### API 27 resource/arena qualification

The previous installation and execution memory barriers are now removed for
the non-radiating encoder qualification module. API 26 is reserved for the
existing experimental MICIN TX callback (standard host supplies a refusing
stub); API 27 appends resource_open and resource_read. These are available
on balanced packed lean live hosts only. Existing modules retain the old
stack convention. An API 27 foreground image opts into the main stack with
a 2,304-byte free-stack admission check and a full arena minus 16 bytes;
LCD flush uses the existing host framebuffer, not an arena callback stack.
The full encoder test has not yet exercised LCD rendering concurrently.

Resource read kind 0 is bounded to the 30,752-byte K6CB header and payload;
kind 1 accesses an explicitly opened K6SC deck. No calibration, raw SPI
write, firmware, or system partition access is added. Deck opening reuses
the host's validated packed directory. The module checks pinned table CRCs
before encoding. A missing or damaged resource aborts without RF.

Live capability bit 22 (resource_deck) permits a signed/tagged K6SC v1
4,182-byte payload in an otherwise eligible free resource/user slot, or
replacement of a matching typed K6SC resource. Normal help cards, modules,
pages, resident objects and reserved slots 9–12 cannot be overwritten by
this operation. Existing 10 KiB ordinary-module live mutations are unchanged.
The Sotto installer selects free slots, reuses exact matching resources,
checks the complete codebook CRC, and never automatically overwrites an
unrelated object or flashes the firmware.

```sh
python3 tools/k6_sotto_module.py --host-resources --out /tmp/sotto-qual
# read-only plan; requires compatible firmware and pinned codebook:
python3 tools/k6_sotto_live_install.py /tmp/sotto-qual --port /dev/ttyUSB0
# explicitly authorize live installation of the two objects:
python3 tools/k6_sotto_live_install.py /tmp/sotto-qual --port /dev/ttyUSB0 --install
```

The resulting SOTTO TEST entry is a non-radiating self-test, NOT the voice
modem. The compiled firmware emulator installed the deck and the module
over live serial, loaded from KUL2, used real SPI callbacks, returned 26
frames and digest a1471c22, and left the full SPI image unchanged during
execution. The independent 10 KiB install/replace/remove lifecycle passed.
The algorithm regression suite retains the old API 5 variant.

Measured resource version:

- 107,712-byte firmware (15,168 bytes of application flash free)
- 9,231-byte module, 9,255-byte signed/tagged envelope
- 4,182-byte deck, 4,206-byte envelope
- 14,400-byte arena; 5,128 bytes of aligned scratch (16-byte end guard)
- 3,984-byte main stack reservation; measured encoder workload high-water
  2,840 bytes, 1,144 bytes remaining; no emulator faults or out-of-range SP.
- About 1,416 bytes of scratch remain after the ~3,712-byte encoder/stage
  state. Do not mistake this for the measured microphone ring requirement.

### Next microphone integration

The existing B-chip real-mic/host-codec/guarded-RF results
(be6edd3a, e3540bd0) remain independent evidence, not an end-to-end on-radio
encoder/capture proof. Upstream micsotto used TIM17 8 kHz sampling and
reported no lost blocks at 48 turns/sample on the original chip. Our
B-chip 200-turn sampler exceeds the real-time budget; characterize shorter
sampler windows, queue depth and worst-case SPI latency before choosing
the production capture method. Reuse the watchdog/vector owner, not a
second remap. This is also the point to integrate the optional TIM16
notification driver: suspend PB7 PWM throughout audio capture and modem
work, preserve flash prefetch when no ISR is needed, and keep the timer
ownership explicit. No physical firmware flash or transmission was made
by this integration pass.
