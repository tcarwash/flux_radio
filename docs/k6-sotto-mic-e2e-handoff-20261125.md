# Sotto live microphone end-to-end RF handoff (2026-11-25)

Status: **firmware and repaired `SOTTO MIC` module installed and read back; physical microphone RF run NOT yet performed**.

## Build and radio identities

- Work branch: `feature/sotto-mic-continuous-pa-20261125`.
- Experimental cable-only K6 Base+Data/API31 firmware: SHA-256 `496c44fcc70710754a74a9853a4c90d4e850d88bd629d3235f70f24e9c5d2bd2`, 122,228 bytes (652 bytes below 122,880 byte ceiling). On-radio build ID `0d3b5123adacc40f`, cable and Rust/C module API ceiling **31**.
- Current compiled live microphone **`SOTTO MIC`** code in slot **30**, API **30**, entry 16, **9,808 bytes**; SHA-256 `904aa97314df74ca87ebcaed40cb84852406d9f1df5de6c5deca06616f2c09dc`. Library envelope was verified by byte-for-byte readback after replacement. The previous 9,756-byte `SOTTO TX` was ambiguous with an older slot-24 module and is preserved separately.
- Current installed envelope: `/tmp/flux-sotto-mic-20261125/slot30-sotto-mic-distinct.k6m`, SHA-256 `7699584d691dc73009a8d6e5f0675c2f7e21fce9529887479129cd55e6081c71`. Previous experimental slot-30 envelope: `/tmp/flux-sotto-mic-20261125/slot30-mic-e2e.k6m`, SHA-256 `52476e80ad9cbf39bb3040c3b35b70ee07770c9502af24119aa843ce99dd4adb`.
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
3. On the K6, launch **CATALOG → SOTTO MIC** (uniquely named **slot 30**, not the older `SOTTO TX` in slot 24). Select the row with **MENU or #**. Confirm screen `SOTTO MIC BENCH`; when the PTT is initially released, make a *fresh sustained press* (>=60 ms), speak a short phrase ~1 second, then release. It should begin transmitting only after that PTT press; it should drain the final END packet and return to RX. Avoid repeated attempts until the monitor report is inspected.
4. Record SDR monitor's packet counts, CRC valid/rejects, exact BEGIN/VOICE/END sequence, decoded PCM, TX LED behavior, and whether the radio returns to idle RX. Save spectrum/IQ/monitor JSON evidence alongside `/tmp/flux-sotto-mic-20261125/` or the monitor evidence tree. **A real microphone TX/RX pass requires measured results**, not only radio TX LED illumination.
5. On failure, do not assume a completed transmission. Note radio/screen/error indication and receiver event timeline. If PTT sticks, release PTT; if needed power-cycle, then inspect. Avoid overwriting the saved slot30 backup.

For a read-only preflight *while the cable is still attached*:

```sh
cd /storage5/dev/flux-sotto-api29-irq-fifo-mock-20261125
~/.local/share/flux-rf-venv/bin/python tools/k6_live.py --port /dev/ttyUSB0 status
~/.local/share/flux-rf-venv/bin/python tools/k6_install.py --port /dev/ttyUSB0 status
```

Release the exclusive K6 bench claim after staging; claim it anew before the user-attended RF run. No automatic mic RF test is scheduled. The installed module is manually activated, and its transmission starts only after a deliberate hardware PTT press.

## Follow-up 2026-11-25: invisible Catalog launch / duplicate module names

- Found duplicate `SOTTO TX` labels in Catalog: older slot **24** (10,156 bytes) and test slot **30** (9,756 bytes). Selection by name alone was ambiguous, especially because failed module runs automatically reopen the Catalog.
- Built and installed the uniquely named **`SOTTO MIC`** module in slot 30. It draws its title immediately **before** codec preflight and shows `CODEC DATA ERROR` for ~1 second if codebook/deck validation fails (previously returned to menu without a display). ARM simulation of the exact new 9,808-byte module passed with **4,104 bytes of scratch available**, PCM RX/TX/RX, exact codec frames, and no FIFO overruns. Full Sotto-specific suite: **202 passed, 12 skipped**.
- With the programming cable still connected, a **real physical-radio** `run-coop 30 --allow-higher-api` launch gave `load=61931845`, `returned=true`, `result=-4`. The `-4` is **expected**: active live serial disables `mic_stream(3)` (the physical PTT test) to avoid treating the shared mic/programming connector as a microphone. It confirms the real C module loader and actual module preflight passed **RAM, chip identity, and Sotto codec/deck** checks; it does **not** demonstrate physical PTT or RF transmission.
- The module was read back exactly after installation; the radio was idle on VFO A 433.500 MHz. Bench lease should be released after this staging.
- For the next real microphone test, unplug the programming cable from the radio, attach the microphone, and run the new **SOTTO MIC** row from Catalog using MENU/#. Its real PTT must be physically pressed and released; neither a cable `run-coop` nor a UART PTT command substitutes for this input. Never run an old `SOTTO TX` row as the intended mic test.

## Follow-up 2026-11-25: Catalog launch succeeds, LCD flush missing

The operator performed a physically initiated Catalog launch while a **read-only, 115200-baud UART logger** was attached. Recorded evidence is under `/tmp/flux-sotto-mic-20261125/catalog-physical-20261125.log`. At `17:07:34 UTC` the actual radio emitted `K6 MENU run row=16`, `K6 LIB load slot=30`, `K6 LIB loaded slot=30 r=9808`, `K6 LIB run slot=30 r=9808`, `K6 MOD run serial=1795626228 code=9808 B`, and `K6 LCD hook registered`. It subsequently returned `-4` (PTT/cable preflight) after 8,923 ms and reopened the Catalog. This **proves the menu and loader worked**. The previous module wrote `lcd_text` but never called `lcd_flush`, so its status and instructions were invisible.

The first multi-screen diagnostic fix increased code to 9,860 B and left too little module scratch for the 1200 codec, which the ARM emulator caught. We therefore kept a **single explicit LCD flush immediately after the `SOTTO MIC BENCH` / `Hold PTT to speak` lines**, removed redundant preflight display code, and retained serial `SOTTO MIC codec` failure telemetry. Final code is **9,780 B**, slot 30, API30, SHA-256 `daeb78c014248d27a9bb15761b1ed87af56498ee08f544af00d94a269fe335e9`, verified on-radio by exact readback, serial `1795626699`, envelope SHA-256 `791de524e9640967f047f855d92e107405dbe765814f372b6003da5b375386a0` at `/tmp/flux-sotto-mic-20261125/slot30-sotto-mic-lcd-flush.k6m`.

The full compiled ARM transmit/receive simulation now intercepts only the **physical LCD SPI/GPIO backend**, since its synthetic boot cannot construct the real main.rs display closure; it still executes the module LCD host callback and host stack switch and asserts the LCD flush was called. The end-to-end mic/codec/FSK/receiver/audio test passes with 4,132 B of scratch, three complete RF frames, zero PCM loss and restored PA. This simulator validates callback invocation but **not actual screen pixels**. A real serial-connected `run-coop 30` on the physical radio returned `load=61931845, returned=true, result=-4` without freezing, as expected from the UART/PTT sharing policy. It did not transmit RF.

**Important test frequency note:** After a module invocation the live status reported VFO A **443.500 MHz**, despite a temporary `k6_live tune 433500000` earlier. The receiver monitor is at **433.500 MHz**. Explicitly verify/set VFO A to 433.500 MHz **using the radio's physical VFO controls** before the attended RF test, or reconfigure the monitor to the actual active VFO; do not assume the previous serial retune persists across a Catalog module session.

Attended follow-up: disconnect cable, attach microphone, launch **SOTTO MIC** (slot 30), verify **`SOTTO MIC BENCH`** now appears, hold PTT, speak briefly, release. Record RF monitor BEGIN/VOICE/END and re-key/RX behavior. The LCD rendering fix is installed but its **physical display outcome awaits the operator's next check**.
