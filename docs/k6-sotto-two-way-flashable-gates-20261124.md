# Sotto two-way **flashable** end state: integration and bench gates

**2026-11-24, research status.** The user requires a *flashable FD6818B K6
transmitter AND receiver* with Sotto installable/selectable as a modem; the
offline voice loop is a qualification tool, not the final deliverable. Do not
label a mock-only loop or an image containing decoder object code as a working
two-way radio.

## Already working offline

- The real compiled experimental API29 Sotto TX Cortex-M0 module runs with
  8-kHz fake microphone PCM, 4,800-baud guarded FSK/FDV1 virtual RF and an
  independent C/Python IQ decoder; real pinned native fixed-point Sotto-1200
  decoder produces the 8-kHz reconstructed receiver WAV.
- Installed-mode selection, mock PTT, bounded control, clean end-of-TX,
  decode, mock speaker 20-ms chunks, and removal-to-FM fallback are tested in
  `tools/k6_sotto_e2e_mock.py`, whose example and known limitations are in
  [the E2E documentation](k6-sotto-end-to-end-mock-20261124.md).
- The end-to-end result is **not** a production Aura process or physical
  receiver. The existing TX runtime is called `SOTTO TX`, whereas main's
  installed-mode recognizer lists `SOTTO TEST` and the opt-in `SOTTO RFTEST`.
  The mock's identity bridge is explicit and not installed on hardware.
- 112 combined focused tests passed with the actual pinned Sotto submodule
  and experimental TX checkout after a clean-room submodule checkout.

## Firmware flash size measurements, current main + this research worktree

Target KD32F328 app budget = **122,880 bytes**. Computed from the compiled
`size.txt` sections `.vector_table + .text + .rodata + .gnu.sgstubs +
.k6_id + .data`, not from guessed zip sizes.

| Build profile | Flash used | Headroom | Status |
| --- | ---: | ---: | --- |
| `build-sotto.sh`: resident decoder/pager, FSK RX disabled, remote UI enabled | 122,896 B | -16 B | Refused by size gate |
| Experimental resident decoder, raw FSK enabled, remote UI disabled | 126,228 B | -3,348 B | Refused by size gate |
| Experimental resident decoder, raw FSK disabled, remote UI disabled | 122,436 B | 444 B | Binary built; audit's hard-coded image name mismatch, and no native RF RX |

The last is **only a footprint probe**, not an approved image. None of these
builds includes a unified installed API30 live Sotto TX module or proves
4800-baud 33-word B-chip RX and speaker playout. Their compilation does
not establish live performance; the compiled firmware and source are not
radio-qualified.

## Required engineering changes before calling this flashable two-way DV

1. **Unify ABI append-only, preserve main's KISS.** Main API26 is read-only
   `mode_info`, API27 is the six-function packet mailbox. Experimental
   live-TX branch uses the *same tail slots* for API26 `micin_tx`, API27
   `resource_open/resource_read`, API28 `fsk_async`, API29 `mic_stream`.
   Do **not** cherry-pick that ABI. Port resources, async TX and microphone
   transport above the main tail (API28+) with explicit version/magic checks,
   update any generated host `pager.rs` API descriptors and module entry
   requirements, and preserve packet-provider isolation.
2. **RX on the actual radio.** Current raw-FSK API supports 1200/2400
   RX, while its 4800 exception is TX-only, 33 words/gain21. A bounded,
   verified B-chip 4800-baud RX service must recover 33-word guarded FDV1
   frames, handle known 0/2-byte prefix displacement and session CRC/epoch,
   and deliver actual compressed Sotto speech into a decoder and PCM/audio
   output, with explicit timeout/rearm/jitter limits and idle restoration.
   The present resident pager's 1200/2400 RF path is not that service.
3. **Flash/RAM budget.** Move receiver DSP into a library module if resident
   RX + Data primitives cannot fit, or measure and reclaim at least 3,348 B
   plus adequate flash headroom without dropping TX watchdog, queue safety,
   verified library, or packet-mbox compatibility. Prove actual executable
   image and module code+stack+scratch fit; avoid speculative unrestricted
   arena borrowing or untested IRQ stacking.
4. **Installable one-touch operation.** The VFO must enumerate the **actual**
   signed installed Sotto package and PTT must launch the unified TX runtime
   only with a real press, with cable/PTT arbitration, PA-off capture and
   bounded lease. Normal Sotto mode should listen for valid sessions while
   idle and play through a bounded speaker buffer. Both directions must be
   independently abortable and cleanup must be checked.
5. **Build-only release gate.** Produce the exact binary + ELF + module
   package + pinned K6CB and K6SC deck, manifest and hashes. Require
   signature/provisioning checks; build audits, API layout tests, exact ARM
   module/receiver mock E2E, KISS regressions and firmware boot/memory
   gates. Explicit status: *flashable test candidate, not RF qualified*.
6. **Physical final gate.** Use the existing Flux **GitHub issue #11** shared
   queue and separate issue #30 FDV1 on-air session, issue #12 microphone
   capture (already prior FSK14 in closed issue #18), with a fresh owner-
   authorized exclusive whole-bench lease, canonical serial lock, bounded
   dummy-loaded transmission, independent IQ scorer, hash-only public
   evidence and explicit slot/VFO/PA/RTC/RX restoration. The existing
   no-RF KISS HIL #33's previous single-use approvals do not authorize
   new TX or flashing.

## Prioritized next implementation checkpoints

1. Deliver the clean end-to-end voice mock on a published feature branch
   and keep this gate sheet tied to #10 (voice program) and #30 (physical
   FDV1).
2. Port the TX API tail safely onto current main without touching API26/27,
   build and emulate the actual firmware host + module interaction.
3. Implement bounded 4,800-baud native FSK receiver and an actual radio
   speaker path; remove the mock-only identity bridge.
4. Recover flash/RAM, build fully auditable flashable two-way firmware;
   only then prepare the immutable hardware manifest for queued testing.

**Safety status:** No actual radio flash, serial access, physical TX, device
module installation or hardware lease occurred during this work.
