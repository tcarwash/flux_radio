# K6 / Aura UI and interaction conventions

Working design guidelines for native firmware, sideloadable modules and the
browser simulator. Prefer consistent muscle memory over clever module-specific
shortcuts. Existing behavior is noted separately from proposed conventions.

## Keys and interaction

| Control | Shared expectation | Current examples / exceptions |
| --- | --- | --- |
| **A/B long hold (600 ms)** | Send the currently selected/listened-to frequency to **VFO B**, activate B and return to Aura; only when a valid target exists | Surf: parked signal; Explore: selected candidate in list. Use the host's `vfo_b_tune` rather than duplicating firmware state writes. |
| **A/B short press** | Keep the screen's established short-press action; never accidentally tune the VFO | Surf cycles caret decay; Explore currently has no short A/B action. |
| **Side keys** | Preserve their existing actions when adding a common shortcut | Explore side A = Try; side B = VFO B handoff. Surf side keys jump between peaks. |
| **UP / DOWN** | Navigate ordered items or tune one step in frequency-oriented views | Explore list selection; Surf channel stepping. |
| **MENU** | Open the view's next level or local menu; keep the meaning visible | Explore toggles scan/list; Surf emits current frequency/level to serial. This is **not yet uniform**. |
| **EXIT** | Leave the module, restoring hardware state; do not leak a held exit press to Aura | Both modules must restore radio/keypad state. |
| **BAND / #** | Prefer consistent band cycling when the module offers multiple bands | Explore accepts BAND or #; Surf uses BAND for bands and # for channel step. Do not silently change these without UI review. |

EXPLORE's **Try** action opens FM receive audio and the speaker with squelch
fully open, so weak candidates can be evaluated. A subsequent key resumes
scanning and restores the previous audio/squelch state; power-off also closes
the temporary listening path. Candidate detection alone remains silent.

## VFO handoff contract

- Only act on a deliberate hold or the screen's existing explicit handoff key.
- Use the selected candidate/parked signal, **not** the current sweep frequency.
- A valid handoff should select VFO B and return to Aura; failure should leave the user in the module and report why.
- Preserve the existing short-press action and existing side-key actions.
- Do not imply repeater TX offset, CTCSS/DCS or transmit readiness were configured when only `vfo_b_tune(rx_hz, mode)` was called. A future richer handoff should have an explicit contract and tests.
- Avoid duplicate actions while a key remains held; release/repress is required for another event. Test around 600 ms and timer wraparound.

## Presentation and feedback

- Show the **current selection** unambiguously (frequency, band, and relevant signal/candidate status).
- Report success or failure succinctly on the radio and/or serial diagnostic channel. Do not replace user-facing feedback with debug-only logs.
- Make active modes (scan, list, parked/monitor, hold, band) visually distinct.
- Preserve a consistent frequency format and units; label receive versus transmit frequencies explicitly.
- Prefer stable selection while background scans refresh, rather than silently jumping to a different candidate.

## Simulator as a first-class radio

- Expose meaningful new controls and states in the browser workbench, not only in unit tests.
- After relevant backend changes, restart the `0.0.0.0:5056` workbench and verify it responds; documentation-only changes do not need a restart.
- Test short/long key presses and key release in emulation when supported. A passing build is **not** an interactive key-test result.
- Keep simulated/bench evidence distinct from actual RF/hardware validation.

## Review checklist for new modules

1. Compare key assignments with Surf, Explore and Aura before choosing new ones.
2. Define tap, hold, repeat and release semantics, including invalid selections.
3. Use shared host APIs for VFO and hardware transitions; restore prior state on exit.
4. Verify behavior in native/emulated tests and, where available, the live browser simulator.
5. Document deliberate exceptions rather than allowing accidental divergence.

This is a **living convention**, not a claim that all current modules already conform.

## Action chirps (firmware API level 22)

- **BEEP** controls the ordinary every-keypress sound. **ACTBEEP** is a separate setting, default ON even when BEEP is OFF.
- A successful hold action uses a brief rising confirmation; an unavailable hold uses a distinct two-note descending response. Both are local speaker tones, never transmitted.
- Modules call `action_chirp(0)` for an available hold action or `action_chirp(1)` for an unavailable hold. The host checks ACTBEEP. Older firmware (`api->level < 22`) simply has no chirp.
- Keep the sound before the action; avoid chirping for a short press. A generic long-key dispatch chirp does not prove that a particular mode handled the action; mode-aware validation remains a follow-up.
- The setting is stored in previously reserved settings byte 161: 0 = legacy/default ON, 1 = OFF, 2 = ON. This avoids changing the 212-byte settings record layout.
