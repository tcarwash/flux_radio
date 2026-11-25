# Sotto live microphone end-to-end RF handoff (2026-11-25)

Status: **firmware and live module installed and read back; physical microphone RF run NOT yet performed**.

## Build and radio identities

- Work branch: `feature/sotto-mic-continuous-pa-20261125`.
- Experimental cable-only K6 Base+Data/API31 firmware: SHA-256 `496c44fcc70710754a74a9853a4c90d4e850d88bd629d3235f70f24e9c5d2bd2`, 122,228 bytes (652 bytes below 122,880 byte ceiling). On-radio build ID `0d3b5123adacc40f`, cable and Rust/C module API ceiling **31**.
- Compiled live microphone `SOTTO TX` code in slot **30**, API **30**, entry 16, 9,756 bytes; SHA-256 `da7ccf20a63b63516e62b6c08111b297a1d0a488a02b5e50a36d426ed70e80c4`. Library envelope was verified by byte-for-byte readback after installation.
- Installed code envelope: `/tmp/flux-sotto-mic-20261125/slot30-mic-e2e.k6m`, SHA-256 `52476e80ad9cbf39bb3040c3b35b70ee07770c9502af24119aa843ce99dd4adb`.
- Original slot-30 module, preserved for rollback before mutation: `/tmp/flux-sotto-mic-20261125/slot30-original.k6m`, SHA-256 `4306f23373a97e663295fad014318781c5560449e8c1729c7904a0cf51964a0f`. This is a **module backup**, not an MCU-firmware backup.
- Firmware build: `/tmp/flux-sotto-mic-20261125/fw-v2/flux-fd6818b-pack.bin`; both artifacts are local to the workstation and **must not be committed**.
- K6 remains on the operator-confirmed dummy load. Post-flash VFO A was restored to **433.500 MHz**, radio idle in RX. VFO B 144.390 MHz is unchanged.
- Existing Sotto 1200 codebook and 4,182-byte typed deck are present from earlier physically qualified on-radio PCM tests. The generic installer warning about missing **600/400** codebooks is not evidence the 1200 codec table is missing.

## Firmware change

The opt-in native `fsk_async` host now follows the bench-qualified FD6818B transmit sequence: first-frame PA enabled *before* FIFO preload, stable 33-word guarded frame, cooperative 20 ms RF startup settle without delaying foreground PCM capture/encoding, PA remains active between frames, FSK disabled only after actual RFIC TX-done interrupt, next frame re-arms FIFO without PA cycling, and all saved registers restored after final explicit `tx_off` or failure/cancel/watchdog. The host provides separate 200 ms startup and 700 ms frame watchdogs, and the existing 25-second module lease remains in force. The ordinary analog and CW TX entry path is unchanged.

Fixed cable capabilities to advertise the same API31 module ceiling as the opt-in Rust host and C module loader. Before this, the image ran API31 internally but its cable record erroneously reported API25, causing the installer to refuse the live module.

## Offline verification

- Native Rust RFIC harness executes the *actual included callback*: nine passing cases for 33-word frames, exact ordering, multi-frame PA lease, abort, forced ready-drop, missing-poll watchdog, IRQ timing/guard model.
- Full Sotto-specific pytest suite: **201 passed, 12 skipped** (skips include unattended hardware/optional simulation gates). Previous FIFO-before-PA timing hypothesis is intentionally pinned to a frozen historical callback, separate from the current source contract.
- Cortex-M0 Unicorn/SoapyFang compiled sender + independent ARM receiver + PCM decoder: **80 ms / 320 ms / 800 ms** sessions pass, respectively 3/4/5 complete valid packets, no PCM loss, no FIFO overruns or underflows, no stuck PA, verified RX audio frame decoding. Simulated 800 ms input peaked at two of four microphone PCM blocks. Actual physical RX speaker output and real mic timing are *not* proven by this simulation.
- No real microphone RF transmissions were made by this session. Earlier independent *generated-tone* physical qualification: 6/6 sessions, 18/18 CRC-valid packets with 20 ms settling and continuous PA.

## Operator-attended live microphone test

1. Confirm K6 is connected to the dummy load, VFO A **433.500 MHz**, battery charged, and RTL-SDR Sotto monitor receiving at the proven hardware center **433.750 MHz**, tuned offset **-30.5 kHz**, fixed 0 dB gain. The monitor is the `/storage5/dev/sotto-monitor` project (USB-reader thread fixed for continuous acquisition). **Do not key into an antenna unless separately authorized for the intended test.**
2. **Unplug the programming/serial cable and attach a microphone/headset.** The Kenwood-style connector's PA11 microphone input is unavailable with the PL2303 programming cable connected. The live Sotto module will fail its microphone preflight if that signal is missing. We cannot monitor its UART while simultaneously using that mic connector on this setup.
3. On the K6, launch **CATALOG → SOTTO TX** (the newly installed **slot 30**, not older slot 24). Confirm screen `SOTTO TX BENCH`; when the PTT is initially released, make a *fresh sustained press* (>=60 ms), speak a short phrase ~1 second, then release. It should begin transmitting only after that PTT press; it should drain the final END packet and return to RX. Avoid repeated attempts until the monitor report is inspected.
4. Record SDR monitor's packet counts, CRC valid/rejects, exact BEGIN/VOICE/END sequence, decoded PCM, TX LED behavior, and whether the radio returns to idle RX. Save spectrum/IQ/monitor JSON evidence alongside `/tmp/flux-sotto-mic-20261125/` or the monitor evidence tree. **A real microphone TX/RX pass requires measured results**, not only radio TX LED illumination.
5. On failure, do not assume a completed transmission. Note radio/screen/error indication and receiver event timeline. If PTT sticks, release PTT; if needed power-cycle, then inspect. Avoid overwriting the saved slot30 backup.

For a read-only preflight *while the cable is still attached*:

```sh
cd /storage5/dev/flux-sotto-api29-irq-fifo-mock-20261125
~/.local/share/flux-rf-venv/bin/python tools/k6_live.py --port /dev/ttyUSB0 status
~/.local/share/flux-rf-venv/bin/python tools/k6_install.py --port /dev/ttyUSB0 status
```

Release the exclusive K6 bench claim after staging; claim it anew before the user-attended RF run. No automatic mic RF test is scheduled. The installed module is manually activated, and its transmission starts only after a deliberate hardware PTT press.
