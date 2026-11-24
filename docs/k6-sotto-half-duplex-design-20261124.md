# Flux K6 Sotto digital voice: half-duplex architecture

**Decision (2026-11-24):** one handheld radio, **half-duplex** Sotto-1200.
Never attempt simultaneous RF transmit and receive. The term "two-way" means
both capabilities on one flashable K6 image, exercised at different times,
not full-duplex voice.

## State contract

| State | Receiver | Mic / encoder | RF transmitter | Speaker |
|---|---|---|---|---|
| FM / Sotto uninstalled | Normal FM | None | Standard FM only | Normal FM |
| Sotto RX/listening (default when selected) | Guarded 4,800-baud FSK, FDV1 receive and session reassembly | Off | Off | Validated Sotto 20-ms PCM blocks |
| Sotto TX (PTT held) | **Off** | 8-kHz mic ISR -> Sotto encoder -> bounded packet queue | Guarded finite 33-word FSK bursts | **Off** |
| TX release / abort | Reset/rearm RX after PA and RF registers restored | Stop / flush or abort with bounded deadline | De-key, restore RFIC, verify idle | Remain muted until RX ready |

**Priority:** PTT always preempts receive and playout; pending playback is
dropped rather than mixed into a local transmit. On entry to TX, stop RX
sampling and clear the RX jitter/PCM queue *before* modifying transmit
registers. On PTT release, complete/abort the finite FDV1 session with a
bounded deadline, stop mic sampling, restore RFIC configuration, verify PA
off and then reacquire RX. If any transition fails, de-key and return to a
safe idle state; never remain transmitting to preserve an audio session.
No background decoder is allowed to write a speaker buffer during TX.

**RAM and code budgeting:** Encoder and decoder **may share mutually
exclusive scratch/arena memory**, because they never execute together.
This is a future optimization opportunity, not a measured reduction: module
loader/runtime and ISR stack boundaries must be audited before unioning
storage. A 3,348-byte firmware flash overrun is separate and **is not fixed**
by RAM sharing; resident RX service and code must still fit in app flash,
or move into an installed module with safe load/unload behavior.

## Offline proof and missing real-firmware boundaries

An [opt-in API31 production-host RX development slice](k6-sotto-api31-rx-development-20261124.md)
now compiles in Base + Data and boots under Unicorn, but its native
Rust callback is not yet exercised end-to-end; its API28–30 slots
remain stubs, so it is not the merged two-way host.

The [incremental fixed-point RX playout model](k6-soapyfang-sotto-lab-20261124.md#incremental-receive-audio-and-bounded-playout)
adds a bounded PCM queue and failure/preemption semantics. Its
host-memory budget is not a verified K6 SRAM measurement.

The [SoapyFang browser lab](k6-soapyfang-sotto-lab-20261124.md)
exposes the full virtual voice loop and accepted receiver PCM for repeated,
parameterized test runs, with source-aware cached compilation. It remains a
separate module bench, not the actual firmware's driver or on-device receiver.

The actual compiled Cortex-M0 Sotto TX code is emulated in Unicorn. The
virtual air receiver runs on the emitted IQ and the *received* CRC/FDV1-
validated compressed stream is decoded with pinned native Sotto1200 C.
The main-compatible API30 branch mock now explicitly transitions
`FM -> RX -> TX -> RX -> FM`, blocks speaker reads during TX, drops
stale queued PCM on key-down, and returns to listening after exception/
overrun. This is a **sequential** mock validation. It is not two operating
physical radios, a simultaneous TX/RX modem or a hardware driver.

Remaining flashable acceptance gates:

1. Bind the actual installed Sotto mode/PTT to the API30 radio host
   callbacks, preserving API26 mode discovery and API27 KISS mailbox.
2. Implement and qualify the FD6818B's *real* 4,800-baud RX and
   33-word FIFO/FDV1 input path on the K6. Guard PTT/RX rearm transitions.
3. Execute Sotto native decoding and speaker PCM playback on the K6,
   including bounded jitter, loss concealment and mode/PTT preemption.
4. Prove the **same** signed firmware and Sotto installable bundle fits
   app flash, arena, stack and timer budgets. 122,880-byte app limit
   remains strict, along with the independent TX watchdog.
5. Qualify dummy-loaded physical RF with a new explicit shared-bench lease
   under Flux queue #11, existing session issue #30, and separate
   microphone issue #12. Do not reuse earlier single-use HIL approvals.

Never label this as a flashable half-duplex working radio until gates 1–4
pass. No live radio action is taken by these tests or state decisions.
