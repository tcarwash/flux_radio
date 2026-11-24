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
