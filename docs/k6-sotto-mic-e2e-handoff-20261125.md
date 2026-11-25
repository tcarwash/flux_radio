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

## Attended RF result 2026-11-25, 443.500 MHz: complete RF/codec packets, silent speech

The operator confirmed **SOTTO MIC launches, the red TX LED stays on while PTT is held, and the SDR monitor displays `voice` packet types**. The initial false negative was a **10 MHz frequency mismatch**: K6 VFO A was 443.500 MHz, while the Sotto Monitor defaults to 433.500 MHz. After setting the monitor to 443.500 MHz with decoder offset -31,300 Hz, the real RTL-SDR receiver recovered a complete session: **1 BEGIN + 26 VOICE + 1 END, 28 CRC-valid packets, 0 CRC rejected, 205 six-byte codec frames, zero sync-bit errors**. The independent monitor observed 4800 baud and ~1.98-2.12 kHz FSK separation. The physical RF packet path now has positive evidence. No serial cable was attached during mic TX, so there is no on-radio mic/TIM17 telemetry for this run.

Saved exact monitor snapshots on the development host:

- `/storage5/dev/sotto-monitor/evidence/mic-live-443500-20261125T1719Z.json`
- `/storage5/dev/sotto-monitor/evidence/mic-live-443500-20261125T1719Z.wav`

**Speech is not yet validated**: although all codec frames pass packet CRC, the PCM WAV is 65,600 samples at 8 kHz (8.2 s), and only 639 samples are nonzero, all in the first decoded 320 ms; later packets decode to exact silence. The 205 recovered 6-byte codec frames have only six distinct codewords; **201/205 are just two alternating codewords** (`1300d89c241d` 101 times; `1302ee09241d` 100 times), consistent with essentially silent PCM input. This suggests a microphone capture/routing/level defect (or an encoder input defect), not an SDR tuning problem. Don't mark audible speech or duplex listening complete until independent microphone sample dynamics or recovered intelligible audio are verified. Next most discriminating experiment is a bounded raw/peak microphone capture with physical mic connected, tracking PA11/TIM17 sample variance under speaking vs silence, plus a test-vector codec sanity check before altering RF timing or packet framing.

The monitor defaults to 433.500 MHz from `Monitor.reset()/Monitor.start()` and does not sync to the radio; manually match its frequency to the active VFO before each physical test. The monitor's -31.3 kHz digital offset is a separate hardware calibration, not a 10 MHz tuning correction.

## 2026-11-25 PA11 sine-wave microphone calibration probe (operator signal generator)

The operator connected a generator to the external microphone pads and verified its tone is audible through normal FM reception at approximately `0.1 V` (peak, peak-to-peak and load impedance **not yet established**). This proves the analog RF mic path accepts injected audio, but **not** that our experimental PA11 one-wire sigma-delta sampler receives it. Do not change chip gain blindly: the host's `mod_sotto_mic_stream(op=0)` already drives the low five bits of `REG_7D` to 31. The more likely fault is signal routing, GPIO PA11 voltage waveform/timing or PCM input to the encoder.

A separate *non-FSK* module was built at `c/k6/mod/sotto_mic_cal.c` (`MIC CAL`, API30, 1,384 B). The test fixture is `tests/test_k6_sotto_mic_cal.py`. It uses the same API30 PTT lease and PA11/TIM17 644-B ring as SOTTO MIC, measures up to one second at 8 kHz, and prints `N`, `LOST`, `PK`, `AVG`, `MIN`, `MAX`, `V` (REG64 VOX), `G` (REG7D gain), `ZC` (sign-change count), `ISR` (TIM17 peak cycles). Its TX lease is PA-muted with no `tx_pa(1)` and **no FSK**. LCD writes occur outside microphone capture. It has a bounded two-second TX lease, one-second sample window, and 12-second results display. `ZC≈2000` is consistent with 1 kHz sine; `N≈8000`, `LOST=0` and nonzero `PK/AVG` indicate digital PCM is responding. Absolute PCM amplitude is not calibrated voltage yet.

Test protocol: ensure dummy load, generator output AC-coupled with **zero externally forced DC offset**, common ground, and measure generator's *actual* peak-to-peak voltage at the microphone pads. Output amplitude labeled for a 50-ohm load may be twice its indicated value into a high-impedance mic input. Don't increase beyond the already working 0.1 V setting yet; if the actual signal is 100 mVpp, optionally run 20 and 50 mVpp later to estimate gain linearity and clipping. Unplug the live UART cable (required for PA11 mic), launch `CATALOG → MIC CAL`, hold PTT ~1-2 seconds, then photograph/read the results. On-camera normal FM reception proves the injected signal is present at the microphone input, *not* at PA11.

The radio had nonempty undocumented data in apparent free slots 1-7. Slot 25 originally contained signed `MIC DIAG` API29 921 B, serial 3, SHA256 for 945-byte envelope `ed402a568726ade8e673872dd7972f47016f5df85a1babb5508963d97748346e`. Its exact image was backed up **before** temporarily installing `MIC CAL` in slot 25, leaving proven `SOTTO MIC` slot 30 intact. Persistent backup and installation details:

- `/home/tyler/flux-bench/sotto-mic-calibration-20261125/slot25-mic-diag-before.k6m`
- `/home/tyler/flux-bench/sotto-mic-calibration-20261125/slot25-mic-cal-v1.k6m`
- `/home/tyler/flux-bench/sotto-mic-calibration-20261125/install-report.json`

Live cable installation was signed and exact readback verified: MIC CAL serial 1795627682, code SHA256 `e82ed68289daf5a87e6068e23acbb4f019b46a6d0d9fffedf27df01b6ca45257`; cable-connected physical `run-coop 25` returned expected `-4` from shared mic/UART exclusivity without transmitting or freezing. After calibration, restore the original 921-B MIC DIAG module by **re-signing its saved code with a fresh monotonic serial** (because the old serial 3 will be rejected), using original level29 and slot25. Verify original code hash and exact readback. Bench was released, original SOTTO MIC slot30 remains serial 1795626699.

## 2026-11-25 physical MIC CAL result: sampled but no 1-kHz PCM tone

Operator photo of the K6's `MIC CAL RESULT` during a verified normal-FM-audible, ~0.1 V generator tone into the external microphone pads (exact Vpp/Vpeak/Vrms still unspecified):

| Field | Observed |
|---|---:|
| N | 7920 |
| LOST | 0 |
| PK | 8357 |
| AVG | 152 |
| MIN | -1948 |
| MAX | 8357 |
| V (REG64 VOX min/max) | 0 / 177 |
| G (REG7D gain) | 31 |
| ZC (PCM sign changes) | 7 |
| ISR (peak cycles per 125-us tick) | 4304 |

Interpretation: the TIM17 ISR runs at essentially its intended 8 kHz sample cadence (7920 produced in the ~1-s window), the ring reports no overrun, and the sampled ISR peak is only ~35.9% of its 12,000-cycle budget. **The sampled PCM does not exhibit a recognizable 1-kHz sine**: a properly DC-centered 1-kHz sine across 7920 samples would have ~1980 sign crossings, versus 7 measured. A large peak alongside AVG=152 might be startup/analog-route transients, not continuous speech. VOX varies while the FM receiver hears the tone: that is evidence the RFIC mic input sees it, not that MCU PA11 receives a waveform. ZC alone cannot exclude a DC-biased or low-SNR tone; future cal can compare tone-on/off and measure 1-kHz correlation or lag-4 differential energy rather than relying exclusively on sign changes.

**Source-level routing hypothesis:** `sotto-api30-mic.rs.inc` currently sets `REG_47` AF selection bits to `0x0400` (AF mode 4), which BK4819 register documentation and this repository's `docs/k6-afsweep.md` identify as raw/baseband output, not a demonstrated TX mic-monitor path. The MCU pin PA11 is documented as the speaker PWM/RC output route, while the external microphone input feeds the FD6818B RFIC. The experimental one-wire sigma-delta sampler can only detect the injected microphone audio if an actual analog coupling route exists from MICP to the PA11 network; it hasn't been demonstrated. Do **not** boost `REG_7D` beyond its already-max setting, infer physical mic PCM from a healthy VOX reading, or change RF registers in TX blindly. Check one tone-off baseline and, if accessible, use a high-impedance scope probe to observe the PA11/RC node while distinguishing analog envelope from MCU-driven sigma-delta pulses. A conservative firmware-only diagnostic may compare per-block lag-4/sample variance and RFIC's other audio output modes with strict saved-register restore. Hardware routing or an alternate mic capture path may be required.

## 2026-11-25 paired tone-on and tone-off MIC CAL experiment

The operator found the injected 1 kHz sine substantially louder on a nearby FM receiver (signal generator still indicated `0.1 V`) and repeated `MIC CAL`, followed by a **generator-output-off** silence test. Both display results were submitted as photographs; voltage convention and actual voltage at the microphone pads remain unknown.

| MIC CAL field | Tone on (louder FM) | Generator off |
|---|---:|---:|
| N | 7920 | 7920 |
| LOST | 0 | 0 |
| PK | 32768 | 8657 |
| AVG (abs PCM) | 1473 | 156 |
| MIN | -32768 | -4080 |
| MAX | 32767 | 8657 |
| VOX (REG64 min/max) | 0 / 517 | 0 / 178 |
| G | 31 | 31 |
| ZC (sign changes about literal zero) | 175 | 5 |
| ISR max cycles | 4304 | 4304 |

A reproducible tone-on/tone-off differential exists: absolute PCM mean rose 1473/156=**9.44×**, VOX max rose 517/178=**2.90×**, and zero crossings rose 175/5=**35×**. This strengthens the hypothesis that some microphone information reaches PA11's experimental sampler. However the tone-on PCM has hit both 16-bit representation limits; `pcm_capture::isr` explicitly clamps the derived PDM/CIC signal to i16, so **digital output saturation is demonstrated, but analog preamp clipping is not yet proven**. With a 1kHz signal at 8ksps for 7920 samples, clean zero-centered PCM would have about 1980 sign changes, not 175. Because this metric uses *literal zero*, DC offset, severe distortion, sparse glitches or poor scaling could distort the crossing count; a direct 1kHz correlation (8-sample periodic I/Q projection) and DC-centered crossing statistic are better discriminators.

Both runs preserved timely ISR service (4304 cycles peak out of 12000) and no queue overrun (LOST 0). Don't interpret the peak as absolute microphone voltage: voltage calibration of the analog microphone path and one-wire PA11 converter has not been established. Keep the known-working `SOTTO MIC` slot30 untouched. Recommended next experiment: generator **20mV and 50mV in the *same unspecified amplitude convention* as the prior 100mV setting**, one-second MIC CAL each, with stable 1kHz sinusoid, same wiring; record average/peak and VOX and listen for normal-FM signal; no need to risk higher voltage. In subsequent diagnostic module versions, add a coherent 1kHz estimator plus a clipping counter and DC-removed RMS/zero crossing; this will resolve whether a 1-kHz component is present despite DC bias. Do not alter the firmware's mic gain register until correlated PCM amplitude and clipping mechanism are characterized.

## MIC CAL v2, live installed, 2026-11-25

**User-approved calibration metric expansion**, not a Sotto transmitter change. `MIC CAL` slot25 is now the v2 module, 1,744 B of ARM code, serial 1795628552, code SHA-256 `8868519ee18ae4e2579544f5221b25ea9338c4b74b9f04b3c5151227867c323a`, verified against physical SPI library readback. Firmware remains `0d3b5123adacc40f`. The prior slot25 MIC CAL v1 and original MIC DIAG images remain backed up; slot30 SOTTO MIC is unchanged. Physical serial-connected launch returned expected `-4` because the serial cable prevents mic capture; **v2 physical tone measurements are not yet collected**.

New metrics visible on LCD during first six seconds of the result screen:

- `1K` = **coherent 1,000 Hz fundamental peak amplitude in PCM counts**, measured relative to the nominal 8 kHz ISR rate. All completed 80-sample blocks are sorted into eight sample phases, excluding the first 160 samples (20ms startup). Phase averages are multiplied by Q8 sine/cosine vectors, and their quadrature magnitude is calculated using integer square root. Both sine and cosine projections have zero total coefficient, so a fixed DC offset cancels. The magnitude is independent of arbitrary generator phase. This is **not a calibrated microphone millivolt measurement** and signal frequency error/drift, clipping, jitter and aliasing may reduce it. In particular, a clipped PDM-derived input may have substantial harmonics even if `1K` is nonzero.
- `CLIP` = **count of PCM samples exactly equal to -32768 or +32767 after 20ms settling**. Detects the ISR's 16-bit numerical saturation, not necessarily an analog preamp overdrive.

Other rows still display `N/LOST`, `PK/AVG`, `VOX/G`, and `ZC/ISR`. At six seconds, row three changes from `1K/CLIP` to the original `MIN/MAX` values for the remaining ~six seconds. PTT remains physically required; RF PA stays muted; no FSK packets or extra peripheral operations during the sample stream. The native-C estimator regression covers 1kHz sine waves with five phases, DC offsets, no tone, and lower amplitude; focused gates: `11 passed, 2 skipped` (simulated hardware gate unavailable). Physical UART cable launch/readback and standby check passed. On radio status afterwards: VFO A=443.500 MHz, `transmitting=false`, `standby_idle=true`.

Exact reinstallation artifacts:
`/home/tyler/flux-bench/sotto-mic-calibration-20261125/slot25-mic-cal-v2.k6m`
`/home/tyler/flux-bench/sotto-mic-calibration-20261125/install-v2-report.json`

Next attended bench sequence: disconnect serial programming cable, generator 1 kHz, use `CATALOG → MIC CAL`; run with output disabled as baseline, then nominal 0.02V and 0.05V settings as already suggested (same generator voltage convention/ground/impedance), keep output amplitude below previous 0.1V setting until PCM gain is understood, photograph first six seconds (`1K` and `CLIP`). Keep dummy load on radio. Compare `1K` amplitude proportionality against generator setting and baseline, and `CLIP` count. If `CLIP > 0` with low input, we should debug the PA11/CIC scaling or settle transients before RF/mic gain tweaks.

## MIC CAL v2 physical amplitude sweep: nominal 0.1, 0.2, 0.5 V (2026-11-25)

The operator submitted three LCD photographs in stated order `0.1 0.2 0.5`. Input voltage calibration (peak vs Vpp vs RMS, scope-verified level, loading and bias) is **still unknown**, so treat these as nominal generator settings, not voltages physically verified at the mic pads.

| LCD field | nominal 0.1 V | nominal 0.2 V | nominal 0.5 V |
|---|---:|---:|---:|
| N | 7920 | 7920 | 7920 |
| LOST | 0 | 0 | 0 |
| PK | 32768 | 32768 | 32768 |
| AVG | 1151 | 1475 | 1491 |
| MIN | -32768 | not shown | not shown |
| MAX | 32767 | not shown | not shown |
| 1K | second page, not shown | 1373 | 1343 |
| CLIP | second page, not shown | 116 | 101 |
| VOX min/max | 0/542 | 0/968 | 0/943 |
| G | 31 | 31 | 31 |
| ZC | 165 | 179 | 177 |
| ISR | 4304 | 4304 | 4304 |

The first capture had already rolled onto the six-second *second page* showing MIN/MAX; its `1K`/`CLIP` values are not available. The other two showed the first page. `CLIP` counts exclude the initial 160 samples, so the rates were **116/7760≈1.49%** and **101/7760≈1.30%**. The fundamental peak amplitude dropped slightly (1373 →1343, **−2.2%**) when nominal source input rose 2.5× (0.2→0.5 V). AVG changed only +1.1%, VOX maximum declined 968→943, ZC stayed near 180 rather than ~2000 for 1 kHz sinusoid at 8 ksps. Some coherent 1 kHz component is detectable but **the PCM output has an effective level plateau and is not a faithful or linear reproduction of the injected sine**. A saturated *analog microphone amplifier* is not proven: the one-wire PA11 CIC/DC reconstruction independently clamps to i16, and the RFIC VOX meter can saturate on its own. No capture-data errors: 7920 samples, LOST0 and ISR4304 across all runs.

**Recommendation:** Avoid increasing the generator beyond the largest already tried 0.5 nominal; decrease instead to **0.02 and 0.05** nominal settings and add an output-OFF baseline using v2's `1K`/`CLIP` figures. Verify actual Vpp with an oscilloscope at the radio's mic pads and confirm zero external DC offset / proper AC coupling; 0.5 V may be very high for an electret mic input. If coherent 1K and CLIP remain comparable at low levels, prioritize PA11/AF route and sigma-delta scaling investigation instead of further mic gain. Prefer measuring the PA11 node with a high impedance scope to distinguish the RFIC microphone input from the MCU's sampled net. Don't reflash, change gain or claim intelligible voice without a controlled raw waveform check. Installed firmware/module unchanged for this documentation result.

## 2026-11-25 software-only gain sweep added, ready for installation

An attended bench at a fixed nominal **0.1 V / 1 kHz generator setting** is requested because the generator cannot output less than 0.1 V. Coherent `1K` values at the larger 0.2 V and 0.5 V settings prove that a repeatable frequency-specific component reaches reconstructed PCM, but still **do not prove clean/integer-linear speech**. The previous five-level nominal-source sweep plateaued at the same 1K output; the RFIC microphone gain setting was already at 31 in all captures.

New independent module **`MIC SWEEP`**, source `c/k6/mod/sotto_mic_gain_sweep.c`, uses the same qualified `tone_1k_amp` math as MIC CAL from parity-checked `sotto_mic_cal_math.inc`. It requests one guarded **PA-muted 4.5 s TX lease without FSK**, requires continuous physical PTT, starts the existing API30 mic-stream ring, and sweeps only **FD6818B `REG_7D[4:0]` gain values `31, 24, 16, 8, 0`**. The compiled host `mod_fd_write` explicitly permits writes to `REG_7D`; the API30 `sotto_mic_stop()` restores the original gain and RFIC route on exit. Each gain is allowed 120 ms to settle while ring data is drained, then measured for 520 ms (~4,160 PCM samples at 8kHz). It computes the 1kHz fundamental peak, clipping count, absolute mean and RFIC VOX maximum per gain. Loss or PTT release cancels the sweep and restores RFIC/PA. LCD results show all five gains and their `1K`/`C` (clipped count) on six lines for 8 seconds, then `A` (mean abs) / `V` (VOX max) on the next page for another 8 seconds. Hold PTT ~4 seconds, then release and photograph both pages; do not need to change the generator between stages. The 1K signal frequency is nominal and a drifted generator can reduce the coherent estimate.

Two test classes: (1) 4 `MIC SWEEP` unit/assembly/guard tests including exact module code linked as position-independent thumbv6m, equality of shared 1kHz math with prior qualified MIC CAL, and safety rejection guards; (2) compiled native **C end-to-end module harness** generating deterministic 8-kHz mic PCM at five gains through an independently mocked API30 ring and RFIC with one entire run from PTT to screen, plus denial test. The host model measured `1K=4599,3200,1900,800,100` for gains `31,24,16,8,0`, `CLIP=0`, completed cleanup, and correctly failed/cleaned up on forbidden gain writes. Focused regression: **15 passed, 2 skipped**. These are synthetic results, **not physical proof**.

Signed cable-compatible staged image: `/home/tyler/flux-bench/sotto-mic-calibration-20261125/slot25-mic-sweep-staged.k6m` (slot 25). The previous slot25 `MIC CAL v2` image and original `MIC DIAG` backup are retained. **The module is not installed yet:** `/dev/ttyUSB0` PL2303 USB converter exists, but live status received no K6 response, consistent with the cable not plugged into the radio's microphone/programming jack while the signal generator is attached. Do not claim physical test or installed status. The radio in slot 30 is unchanged. Installation requires operator to temporarily connect the program cable, with TX idle and an exclusive bench lock. Before installation, compare existing slot25 readback exactly to `slot25-mic-cal-v2.k6m`; refuse to replace a different module. After installing, verify exact readback and test physical cable run expecting `-4` from mic-stream/PTT exclusivity. Then user can reconnect the signal generator and run `CATALOG → MIC SWEEP` on the dummy load.

## MIC SWEEP physical installation completed, 2026-11-25

With the external K6 programming cable reconnected, live status initially showed TX idle but `standby_idle=false` (the operator's menu was open). A single validated live `key EXIT` restored standby. We reserved the shared bench as `flux-mic-gain-sweep-install` and installed the staged 1,608 B code image into **slot 25**, checking original slot25 exact image equality (`MIC CAL v2`, 1,768 B envelope, SHA-256 `8fcbddb9f59a55ebc9f1e6b2bc0ca6654072c9acd6d989dbb6fef273fae5090c`) before any write. Installed staged `MIC SWEEP` serial **1795629633**, envelope SHA-256 **`36c82c821e0b805e56cbd89647abd3264933f177d07b0d71d20813b4551bf81a`**, code 1,608 B. Readback matched byte-for-byte. Slot 30 `SOTTO MIC` was verified unchanged before and after installation, SHA-256 `791de524e9640967f047f855d92e107405dbe765814f372b6003da5b375386a0`. The radio is still running firmware `0d3b5123adacc40f`.

A cable-connected `run-coop 25` physically loaded 1,608 B and returned `-4` as expected from the microphone/PTT shared connector; it did **not** freeze or transmit. Post-check: `transmitting=false`, `standby_idle=true`, VFO A=443.500 MHz, battery=7.73V. Exact installation log and module backups live in `/home/tyler/flux-bench/sotto-mic-calibration-20261125/`, including `sweep-install-report.json`, `sweep-cable-preflight.log`, original `slot25-mic-diag-before.k6m` and `slot25-mic-cal-v2.k6m`.

**Attended final test remains outstanding:** unplug programming cable, connect external generator at **1 kHz / nominal 0.1 V**, leave radio on dummy load, enter `CATALOG → MIC SWEEP`, hold PTT continuously about **4 seconds** until radio returns, then photograph the first LCD results page (`GAIN 1K CLIP`: gains 31,24,16,8,0) and after 8 seconds the second page (`GAIN AVG VOX`). These are **physical** gains/results, not the deterministic synthetic tests previously captured. Don't infer gain linearity before this hardware experiment.

## MIC SWEEP first attended physical result: gain curve shows baseline-only PCM

The operator submitted two photographs after a real run of installed `MIC SWEEP` (slot25), with all five gain stages complete and the two LCD result pages present:

| Gain | 1K | C (16-bit saturated samples) | A (mean absolute PCM) | V (RFIC VOX maximum) |
|---:|---:|---:|---:|---:|
| 31 | 0 | 0 | 1 | 192 |
| 24 | 0 | 0 | 1 | 181 |
| 16 | 0 | 0 | 1 | 192 |
| 8 | 0 | 0 | 1 | 201 |
| 0 | 0 | 0 | 1 | 200 |

These are **real hardware measurements**, not the offline host fixture's synthetic gain response. All five gain rows printed rather than errors, so register writes were apparently accepted and the ring delivered enough samples per gain to avoid `ERR` (the current UI does not display sample counts or loss; firmware only checks them internally). `1K=0`, `C=0`, `A=1` at *all* five gains indicate virtually no measurable reconstructed analog waveform. VOX maximum ~181-201 matches the earlier known generator-OFF test `VOX max=178`, unlike previous tone-ON runs VOX ~517-968. **Leading hypothesis:** the generator output was disabled/disconnected or the mic-pad path was open during the sweep. Confirm external generator output was ENABLED and its tone actually audible on a nearby normal-FM receiver in the same setup before attributing the result to mic-gain ineffectiveness. An alternate possibility is that `fd_write(REG_7D)` while the sampler is active changes RFIC state or suppresses AF routing; even the first gain31 re-writes this register. Distinguish these by adding a gain31 no-write control before software gain changes if repeated tone-ON sweep reproduces silence.

Importantly, these results **do not establish** that RFIC gain affects or fails to affect microphone PCM, or that Sotto MIC is fixed. The previous MIC CAL at 0.1 nominal source reported AVG=1151, VOX max=542 and clipping, so the new all-zero run is *not directly comparable* until verified constant input. `SOTTO MIC` slot30 and firmware unchanged; bench lock was free after the test.

## 2026-11-25 second physical MIC SWEEP, tone verified at RFIC but PCM silent; A/B diagnostic staged

The operator provided *another* two LCD photos of installed slot25 MIC SWEEP with source-tone conditions. The LCD values were:

| Gain | 1K | C | A (mean abs PCM) | V (VOX max) |
|---:|---:|---:|---:|---:|
| 31 | 0 | 0 | 1 | 536 |
| 24 | 0 | 0 | 1 | 549 |
| 16 | 0 | 0 | 1 | 537 |
| 8 | 0 | 0 | 1 | 576 (approximately; image is slightly blurred) |
| 0 | 0 | 0 | 1 | 548 |

This differs materially from the first generator-off sweep: RFIC VOX max was only ~181-201 in the first sweep. **VOX now reads ~536-576, comparable to the previous MIC CAL `0.1 V` tone-on result 542, yet post-sweep PCM metrics remain essentially zero at all gains**. Hence we have positive RFIC tone-detection evidence while the PA11 sigma-delta PCM sampler reports silence. Do not infer that gain calibration solved anything; the gain-vs-PCM response is not yet interpretable. The original v2 `MIC CAL` at 0.1 V previously recovered mean abs PCM 1151, whereas the installed gain sweep performs a `REG_7D` write (even for gain31) immediately after microphone stream activation. This suggests two testable hypotheses: (a) rewriting REG_7D while stream is live suppresses the PA11 analog path; (b) a software handling difference in MIC SWEEP (ring readout/settling) suppresses reconstruction. There is no evidence of FSK/RF transmit in the calibration.

Added an **independent, small `MIC A/B` slot25 module** for a controlled three-stage experiment, with **same PTT hold, constant generator, same sampler and signal math for each stage**:

1. `B31`: gain31 with *no call to fd_write* after `mic_stream(0)` (negative-control baseline)
2. `W31`: write the *identical* REG_7D value with gain31 via `fd_write`, then sample
3. `W08`: write gain8 and sample

Each stage discards 120ms settling and measures 620ms; one 3.5-second PA-muted lease, no FSK and no RF PA, same contract as MIC SWEEP. LCD shows 3 rows of `1K` and `C` (clip) for 8 seconds, then AVG/VOX for 8 seconds; `B=no-write` and `W=write` are printed as reminders. In the first stage, the module only reads REG_7D, which the previous MIC CAL also did. It restores via host `mic_stream(1)` and `tx_off` on every error or successful end. The host-native test executes the actual C module and verifies a simulated 1K peak ~4599 for both baseline and same-value write, ~800 at gain8, no clipped samples, no leaking PTT or mic lease, and safe teardown if RFIC writes are rejected. The PIC ARM module builds at level30; focused tests: **17 passed, 2 skipped**. Synthetic test results do *not* verify physical silicon behavior.

Staged artifacts on tyler-ldesktop (the radio is still connected to the signal-generator adapter, so no UART programming connection was available; no installation was attempted):

- `/home/tyler/flux-bench/sotto-mic-calibration-20261125/slot25-mic-ab-staged.k6m`
- `/home/tyler/flux-bench/sotto-mic-calibration-20261125/mic-ab-staging-report.json`

The previous installed slot25 MIC SWEEP envelope is backed up at `/home/tyler/flux-bench/sotto-mic-calibration-20261125/slot25-mic-sweep-staged.k6m` and must match slot25 readback before replacement. Slot30 `SOTTO MIC` and base firmware are **unchanged**. To install, operator temporarily reconnects USB cable, bench is reserved, verify radio standby and exact slot25 equality, write signed slot25 envelope, exact readback and cable-connected no-PTT preflight, release bench. Then restore generator to 1kHz / nominal 0.1 V, confirm RFIC tone (VOX around 540), hold PTT ~3 seconds and photograph both pages. `B31 >0, W31 ≈0` would strongly implicate the REG_7D write. `B31≈0` too means there is likely a difference between MIC CAL and MIC SWEEP sampling or changed PA11 hardware state. Don't modify firmware gain or RFIC AF modes until this comparison is obtained.

## 2026-11-25 MIC A/B installed and physical cable preflight verified

After the operator reconnected the radio's programming cable, live serial on `/dev/ttyUSB0` responded. The radio was TX idle but in a menu (`standby_idle=false`), so a **single short EXIT** returned it to standby. We reserved the bench as `flux-mic-ab-install`. Exact slot25 readback matched the saved `MIC SWEEP` envelope SHA256 `36c82c821e0b805e56cbd89647abd3264933f177d07b0d71d20813b4551bf81a`; slot30 `SOTTO MIC` readback matched SHA256 `791de524e9640967f047f855d92e107405dbe765814f372b6003da5b375386a0` before installation. Installed `MIC A/B` from the staged envelope with serial `1795631510`, code 1,700 bytes, envelope 1,724 bytes, SHA256 `7cc29474e69f185ef47c67a861335c53a848ea020fbba96919a1397b7fc7a622`, library slot25/API30. Verified exact slot25 readback matches staged envelope and slot30 remains unchanged. Physical connected `run-coop 25` returned `load=1700, returned=true, result=-4`, the **expected failure** due to shared microphone/live-UART connector; there was no freeze, no transmission or RX change. Subsequent live status showed `standby_idle=true`, `transmitting=false`, VFO A=443.500 MHz and 7.73 V battery, and module listing confirmed MIC A/B 1700 B in slot25 and SOTTO MIC 9780 B in slot30.

Persistent logs and artifacts: `/home/tyler/flux-bench/sotto-mic-calibration-20261125/mic-ab-install-report.json`, `mic-ab-cable-preflight.log`, signed `slot25-mic-ab-staged.k6m` plus the saved previous `slot25-mic-sweep-staged.k6m`, `slot25-mic-cal-v2.k6m`, and original `slot25-mic-diag-before.k6m`. Exact installation and cable-preflight verification are complete; no live generator sampling has occurred with MIC A/B yet. After operator **disconnects the programming cable and reconnects the generator to microphone jack at 1 kHz/nominal 0.1 V**, run Catalog `MIC A/B`, **hold physical PTT continuously at least ~3 seconds** until all three stages finish. Photograph first page with `B31` (no write), `W31` (same gain write), `W08` (gain8 write): `1K`/`C` values. After ~8 s, second page gives `A` (mean absolute PCM) and `V` (VOX max) for the same three stages. Dummy load must remain attached. No RF PA enable or FSK in this diagnostic. The physical gain-register hypothesis still awaits these attended results.

## 2026-11-25 module diagnostic mailbox: capture with mic cable, retrieve later over live UART

Operator requested **serial retrieval after a physical microphone/generator test**, so photographs are no longer required for every result. Existing `a->say` only emits reports immediately and loses them if no UART is attached; API4 `cal_write` erases a shared 4 KiB calibration sector, and API5 `fl_prog/erase` can write only the 64 KiB repeater table. **Neither existing flash range is appropriate for diagnostic results.** No test output is ever written to the calibration, repeater-table or user SPI areas by this implementation.

Implemented an opt-in **boot-local SRAM diagnostic mailbox** on firmware profiles with `k6_sotto_mic`. A completed `MIC A/B` run (new module code in `c/k6/mod/sotto_mic_ab.c`) invokes its already-supported `say()` callback with `MAB1:` plus 44 base64 characters containing a 33-byte, versioned and CRC16/XMODEM-protected result record: three stages (fixed order B31 baseline/no-write, W31 same-gain write, W08 gain8 write), each with 1-kHz PCM peak, clipped sample count, mean absolute PCM, max RFIC VOX, and PCM sample count. A fresh attempt sends `MAB1:CLR` so an aborted or failed sweep cannot be mistaken for new results. After the PA and microphone stream have been stopped, the firmware `mod_say` intercept stores only a strictly validated 44-character base64 string in SRAM, using no flash writes, under `firmware/k6-aura/apply-mic-result-serial.py`. The live serial dispatcher adds **read-only command `0x86`**, which returns the current valid record or an explicit empty response. It refuses reads while busy. The user-facing CLI is:

```sh
python3 tools/k6_live.py --port /dev/ttyUSB0 mic-result
# Optional create-only JSON export:
python3 tools/k6_live.py --port /dev/ttyUSB0 mic-result \\
  --save ~/flux-bench/sotto-mic-calibration-20261125/mic-run-01.json
```

`tools/k6_mic_result.py` independently verifies base64, version, CRC and per-stage sample-count invariants before reporting values. Values are raw **PCM counts**, not calibrated input volts. A returned empty mailbox is *not* a zero-valued test. The CLI adds the computer's retrieval timestamp, **not a capture timestamp**. No raw PCM is saved by this tiny record. Retention is only **until the next MIC A/B run or a radio power cycle/reset**; reconnecting/disconnecting the microphone and USB cable while leaving power on does not clear it. Results are not silently uploaded or exported elsewhere, and the reader never invokes a module or keys RF.

The module wire format and end-to-end read decoder were validated using the real compiled C module in an independent host microphone/RF mock. The synthetic fixture says `MAB1:` + 44 chars, and Python decodes the same record with verified CRC and exact stage numbers. New tests: `tests/test_k6_mic_result.py`. Firmware builder now invokes `apply-mic-result-serial.py` with the API30 microphone overlay and owns canonical firmware changes in `firmware/k6-aura/live-serial.rs`. Live tool CLI enhanced in `tools/k6_live.py`. No API v31 layout changes.

**Offline firmware build completed (not flashed):** `/home/tyler/flux-bench/sotto-mic-calibration-20261125/mailbox-v1/flux-fd6818b-mic-mailbox.bin`, SHA256 `231f5167afb5d11015c829e2d0b72f810d38a35a2729d159e5d2dfce54411603`. ARM image uses **122,504/122,880** flash bytes (376 B margin), 14,976-byte arena, protected RAM0 free **44 B**, and main stack reservation **2,792 B** (audit floor 2,780). Existing Sotto compiled ARM microphone→FSK→receiver gate remains **PASS**, with PA restored, zero PCM loss, valid frames and module LCD callback. Existing `MIC A/B` on-radio in slot25 **does not yet emit mailbox records**; to activate this, both the new firmware and updated slot25 module must be installed by an explicitly planned physical flash and SPI slot update. Slot30 SOTTO MIC stays untouched by the staged build. Signed updated module file is `/home/tyler/flux-bench/sotto-mic-calibration-20261125/mailbox-v1/slot25-mic-ab-with-serial-result.k6m`; validate the serial watermark at installation time and re-sign with a fresh serial if needed.

Focused serial/MIC suite: **82 passed, 5 skipped, 2 deselected**. The two deselected legacy `k6_talk` tests require an unavailable optional `soundfile` Python package; their initial run failed on `ModuleNotFoundError`, not protocol regressions. The new module/decoder tests passed. These are *offline* validation only; the true radio-side persistence/serial cable-swap test is pending a firmware flash and an attended 1 kHz / nominal 0.1 V generator run. Existing earlier physical results cannot be retroactively retrieved in this new format.

Activation steps: (1) reserve bench, verify radio idle, back up as appropriate; (2) flash staged firmware image with normal dev cable protocol; (3) verify live status, build ID, current SPI modules, read-only empty mailbox; (4) verify slot25 exact previous MIC A/B envelope SHA256 `7cc29474e69f185ef47c67a861335c53a848ea020fbba96919a1397b7fc7a622`, then install signed new module in slot25, byte-identical readback and verify slot30 unchanged; (5) perform cable-connected preflight, expecting PTT -4 and an empty mailbox; (6) disconnect cable, inject generator, run MIC A/B holding physical PTT ~3 seconds, reconnect cable without reboot, issue `mic-result --save`. Preserve firmware rollback candidate in the same `mailbox-v1/` folder.

## 2026-11-25 MIC A/B mailbox production radio flash COMPLETE

The operator explicitly approved flashing the staged mailbox firmware. Bench reserved `flux-mic-mailbox-flash`; source HEAD `aee2640f`, working tree clean at start, original radio battery 7.73 V TX idle, previous build ID `0d3b5123adacc40f`. Pre-flash exact module readbacks: slot25 MIC A/B 1700B envelope SHA256 `7cc29474e69f185ef47c67a861335c53a848ea020fbba96919a1397b7fc7a622`; slot30 SOTTO MIC 9780B envelope SHA256 `791de524e9640967f047f855d92e107405dbe765814f372b6003da5b375386a0`.

The **full 2,097,152-byte SPI flash backup** succeeded, with calibration-sector read-back checked twice, at `/home/tyler/flux-bench/sotto-mic-calibration-20261125/mailbox-v1/preflash-spi-20261125T1903Z.bin`, SHA256 `6a834d2d7f6bd07bd787ea804e7bd47deea4530d426e996adf719b783ad9134d`.

**MCU firmware flash SUCCESS:** `tools/k6_install.py --port /dev/ttyUSB0 flash .../flux-fd6818b-mic-mailbox.bin` entered UPDATE mode and completed all 120/120 KiB chunks, reporting successful reboot. The radio initially did not respond to live serial after the automated reboot; following an operator status report that the radio had returned, live serial again responded. It was temporarily in a non-standby UI state; one remote `EXIT` key restored `standby_idle=true` without transmitting. Postflash build ID is **`eb168fb82d789a65`**, live command `mic-result` returned a valid **empty** mailbox (`available=false`), confirming command 0x86 on the physical radio.

**SPI module slot25 replacement SUCCESS:** verified previous slot25 image unchanged, new signed MIC A/B result-mailbox module installed in slot25, serial `1795633209`, 2068B code (2092B envelope), SHA256 `b0506c2cb93e31b3e673497db381e916c1c6b4319536e5c43d75ed1ced97be30`; exact post-install readback matched staged bytes. Verified slot30 remained byte-for-byte unchanged (`SOTTO MIC`, 9780B). Cable-connected remote `run-coop 25` loaded the new 2068B module, issued `MAB1:CLR`, and returned `-4` due to physical microphone/UART exclusion, as expected. Follow-up `status` showed standby idle, TX off, 7.73 V battery, VFO A 443.500 MHz, while `mic-result` showed valid empty state and `k6_install.py status` confirmed both module slots and build ID. This **does not yet validate the success-path mailbox payload from physical microphone measurements**; only the read API and cable preflight are proven on radio.

Persistent verification reports/logs under `/home/tyler/flux-bench/sotto-mic-calibration-20261125/mailbox-v1/`: `firmware-flash.log`, `postflash-mic-ab-install.json`, `module-cable-preflight.log`, `final-live-status.json`, `final-empty-mailbox.json`, plus image and backup above.

Next operator test: disconnect programming cable, attach generator to external mic pads, set 1kHz at nominal 0.1V with generator output on; dummy load attached. Launch `CATALOG → MIC A/B`, hold physical PTT for ~3 seconds to complete B31 baseline, W31 same-gain write and W08 gain8 stages. Reconnect serial programming cable **without power cycling radio** (RAM mailbox clears on reboot) and send notice. Agent runs `python3 tools/k6_live.py --port /dev/ttyUSB0 mic-result --save /home/tyler/flux-bench/sotto-mic-calibration-20261125/mailbox-v1/mic-ab-live-01.json`, verifies CRC and reports B31/W31/W08 with per-stage `1K`, `C`, `A`, `V`, sample count. Display photos are optional. Next run clears the mailbox, so retrieve before starting another test. No further firmware update is required for this test.

## 2026-11-25 first end-to-end boot-local serial result retrieval, MIC A/B

After the firmware build `eb168fb82d789a65` and the v1 mailbox module (slot25 serial `1795633209`) were installed, the operator ran a tone-injected physical test with the programming cable unplugged, then reconnected the cable **without resetting**. The initial read reported `BUSY` because the radio was in the lingering MIC A/B result UI; one remote `EXIT` returned it to standby without rebooting. `tools/k6_live.py --port /dev/ttyUSB0 mic-result --save /home/tyler/flux-bench/sotto-mic-calibration-20261125/mailbox-v1/mic-ab-live-01.json` then successfully decoded the **valid CRC-checked SRAM record**, proving the entire store-across-cable-swap-and-read workflow on real physical hardware. Retrieval timestamp (not actual capture time) 2026-11-25T19:08:27.455969+00:00.

| Stage | After REG_7D write? | 1K PCM peak | Clipped | Mean abs PCM | VOX max | PCM samples |
|---|---|---:|---:|---:|---:|---:|
| B31 gain31 | NO | 0 | 0 | 1 | 539 | 4960 |
| W31 gain31 | YES | 0 | 0 | 1 | 544 | 4960 |
| W08 gain8 | YES | 0 | 0 | 1 | 540 | 4960 |

The previously suspected `REG_7D` rewrite isn't sufficient to explain silence: **B31 did not write the register and also measured silence**. VOX~540 at every gain is consistent with the physical tone-present RFIC path. The PCM ring produced 4960 samples in each 620ms measured stage without error; no clipping or correlated 1kHz tone was present after each stage's first 120ms settling interval. The earlier MIC CAL captured its entire ~1s including the first 20ms for amplitude means, and used only a 20ms skip for coherent/clipped counters; unlike MIC A/B, it did **not** discard the initial 120ms after `mic_stream(0)`. One plausible explanation is that the earlier MIC CAL's apparent strong PCM 1kHz/amplitude was dominated by **the initial route-switching/transient period**, and the PA11 output falls to near digital zero in steady state. This is a hypothesis, not yet proven: true per-window early-vs-late samples were not saved. Distinguishing experiment: new diagnostic records 0-40, 40-120, 120-400, 400-1000ms PCM absolute mean, clipped counts and 1kHz amplitude, ideally comparing the original and A/B acquisition loops, without changing the same RFIC route or raising generator voltage. The zero value of the steady-state quadrature fundamental points away from merely insufficient RFIC gain, and toward the one-wire PA11 sampler/AF-routing or initial transient artifacts. Avoid marking speech intelligible from the CRC-valid `VOICE` RF frames.

Successful bench result JSON is persisted on the workstation and is accessible without another experiment. The radio remains on VFO A 443.500MHz, TX idle and standby; bench is free. SRAM record persists until next MIC A/B attempt or reboot, but its JSON has been saved permanently.

## 2026-11-25 follow-up: 20ms time-resolved microphone diagnostic (MIC TIME, v2 mailbox)

The real MIC A/B physical run showed the same near-zero reconstructed audio at gain 31 **with no register write** as at gain31/8 after a gain-register write, although all phases collected 4,960 PCM samples and their RFIC VOX maxima were 539/544/540. That falsifies a simple `REG_7D`-write-only explanation. Prior `MIC CAL` captured strong but abnormal PCM/clipping/1-kHz energy in an *unsettled* one-second acquisition; MIC A/B ignored the first 120ms and saw only silence. We therefore need a time profile from the **very first reconstructed sample**, with a constant gain31 and no AF-route/gain modifications during the test.

`c/k6/mod/sotto_mic_time.c` provides `MIC TIME` (API30), a drop-in diagnostic for slot25. It maintains the exact same PA-muted `tx_on` microphone lease, uses the existing RFIC routing, shared 8-phase 1-kHz I/Q estimator, and 80-sample ring, but **does not call `fd_write` or change gain**. The module holds an uninterrupted approximately one-second sampled capture. It measures twelve nonoverlapping time windows relative to the **first valid ISR PCM sample after three TIM17 warm-up ticks**: `0–20,20–40,40–60,60–80,80–100,100–120,120–200,200–320,320–480,480–640,640–800,800–1000 ms`. The first six 20ms bins discriminate startup-only sample energy and tonal content, while six progressively wider bins check whether audio continues to one second. Metrics per bin: mean absolute reconstructed 16-bit PCM and coherent 1kHz PCM peak, each logarithmically 8-bit quantized with approximately <=6.25% coarse granularity; global PCM clip count, RFIC VOX maximum and total PCM samples are preserved as 16-bit exact counts.

The existing boot-local mailbox `0x86` and `MAB1:` base64 transport are reused **unchanged**, avoiding another 120KiB firmware flash. Payload is still exactly 33 bytes and validated by CRC16/XMODEM: record version 2; 12 pairs of quantized bin amplitudes; 2-byte total clip count; 2-byte VOX max; 2-byte sample count; 2-byte CRC. `tools/k6_mic_result.py` decodes version 1 MIC A/B and version 2 MIC TIME, preserving historical v1 behavior. The CLI command stays `python3 tools/k6_live.py --port /dev/ttyUSB0 mic-result --save <newfilename.json>`. New `tests/test_k6_sotto_mic_time.py` and native fixture `tests/data/k6/sotto_mic_time_host.c` run the **actual C module** against synthetic continuous 1kHz PCM, tone for only 0-120ms, near-silence, and UART/PTT-exclusion scenarios; tests assert phase amplitude progression and clean PA+ISR teardown, validate CRC/decoder, require the real ARM PIC compilation, and guard against FD6818 write/PA/FSK/flash programming in module source. Offline focused regression after modifications: **26 passed**.

This does **not** directly count raw GPIO PA11 sigma-delta `0/1` bits. API30 only exposes reconstructed i16 PCM and an ISR timing peak; collecting raw PA11 bit statistics would require a further firmware service change and an additional radio flash. The time profile is the safer discriminator to run first. If all windows are nearly zero, the original `MIC CAL` mean/1kHz results have an unexplained methodological or physical-state discrepancy rather than a confirmed transient. If early bins are high and late bins collapse, the transient hypothesis gains real evidence. Sustained 1kHz energy across late bins would point to gain sweeper stage handling rather than a fundamentally absent PA11 mic route.

Radio operating procedure: with the radio idle and programming cable attached, install only signed `MIC TIME` into library slot25 after byte-identical previous-image verification, preserving slot30 `SOTTO MIC`. Run the cable-connected preflight (expected `-4` due to shared UART/mic connector). Then the operator disconnects cable, attaches stable 1kHz/nominal 0.1V generator, keeps dummy load connected, runs `CATALOG → MIC TIME` with PTT held for **at least 1.5 seconds**, and waits for `MIC TIME SAVED`. Reconnect serial cable **without radio power cycling** and use `mic-result` after radio returns to standby. The version2 result is verified/saved on host. Bench lock is held only for installation, not the attended generator test.

### MIC TIME installed on physical K6 and cable preflight passed

Committed source as `916302ed`, built/tested as a standalone API30 K6 ARM PIC module (native host tests: 26 focused passing). Bench reserved `flux-mic-time-install`. Physical preflight showed radio standby idle and 7.74 V battery, with base firmware build ID `eb168fb82d789a65` and old signed MIC A/B in slot25, SHA256 `b0506c2cb93e31b3e673497db381e916c1c6b4319536e5c43d75ed1ced97be30`. The previous completed MIC A/B record was read again and independently saved as JSON at `.../mailbox-v1/mic-ab-live-01.json` before replacement.

The standalone `MIC TIME` module (source `c/k6/mod/sotto_mic_time.c`) is now **installed in slot25**. Signed envelope serial `1795634295`, code 1992 bytes, envelope 2016 bytes, SHA256 `e0d68f45d8b9258c6d9bab9aa43afacb9032fc65634a29e33945af69f45026b1`, with exact SPI readback against the staged envelope and unchanged slot30 `SOTTO MIC` SHA256 `791de524e9640967f047f855d92e107405dbe765814f372b6003da5b375386a0`. The radio's base firmware was not reflashed. Staging/backups and reports: `/home/tyler/flux-bench/sotto-mic-calibration-20261125/mailbox-v1/mic-time-v2/` (`slot25-mic-time-v2.k6m`, `install-report.json`, preflight logs). Cable-connected `run-coop 25` loaded the 1992B module, sent `MAB1:CLR`, returned expected `-4` because UART occupies the mic connector; subsequent status remained standby idle/TX false and mailbox read returned valid empty. The true on-radio 1kHz time-series still requires the physical cable-swap and generator run.

Operator instructions: with dummy load attached, remove programming cable, connect generator at nominal 0.1 V, 1kHz output ON, select `CATALOG → MIC TIME`, hold physical PTT for **at least 1.5 seconds** and watch for `MIC TIME SAVED`; then reconnect programming cable **without rebooting**. The assistant can query `mic-result --save /home/tyler/flux-bench/sotto-mic-calibration-20261125/mailbox-v1/mic-time-live-01.json` and interpret 12 time windows. The known-good `SOTTO MIC` slot30 remains untouched, and the bench lock is released for the user test.

### MIC TIME first physical result: transient ends by ~100ms (retrieved 2026-11-25 19:46Z)

Operator completed the attended constant 1kHz/nominal 0.1V signal-generator test, then reconnected the programming cable without cycling radio power. The radio initially reported UI not in standby (but TX off), so a short remote EXIT returned it to idle. `python3 tools/k6_live.py --port /dev/ttyUSB0 mic-result --save /home/tyler/flux-bench/sotto-mic-calibration-20261125/mailbox-v1/mic-time-live-01.json` decoded the CRC-valid **v2 time-resolved mailbox** from real radio RAM. The permanently saved host JSON SHA256 is `340ef23930eb7400df6f787fcc5100a6241091e796919a155965a81f49452d04`; its retrieval timestamp is `2026-11-25T19:46:32.238012+00:00`, which is **not** the time of capture. `n=8000` produced i16 reconstructed PCM samples in 12 bins; total 92 clipped samples; max RFIC `REG_64` VOX **893**. The quantized per-bin numbers are:

| Time since first valid PCM | Mean abs PCM | Coherent 1kHz peak |
|---|---:|---:|
| 0–20ms | 14848 | 22528 |
| 20–40ms | 23552 | 32768 |
| 40–60ms | 12800 | 17408 |
| 60–80ms | 3968 | 5120 |
| 80–100ms | 160 | 152 |
| 100–120ms | 1 | 0 |
| 120–200ms | 1 | 0 |
| 200–320ms | 1 | 0 |
| 320–480ms | 1 | 0 |
| 480–640ms | 1 | 0 |
| 640–800ms | 1 | 0 |
| 800–1000ms | 1 | 0 |

This **confirms actual startup-only reconstructed PCM** with strong early measurements and digital saturation, then near-perfect steady-state silence by ~100ms, notwithstanding tone-present RFIC VOX. It also reconciles the previous MIC CAL nominal one-second mean and tone estimator with the MIC A/B gain sweep after a 120ms discarded setup window. `tone_1khz_pcm_peak` is a coherent 8-phase estimate, not proof of a clean, sustained microphone waveform; high transient energy may project onto it during a short 20ms window. The PA11 sigma-delta implementation explicitly drives and reads GPIOA11 each ISR turn; RFIC `REG_47` currently selects AF=4 (`BASEBAND1`/raw), whereas `docs/k6-afsweep.md` documents this register as **speaker/receive AF output selection**, not a confirmed microphone-monitor DAC route. PA11 also is the MCU speaker PWM/RC net (`docs/k6-stock-firmware.md`). A reasonable leading *hypothesis* is a speaker-output or routing/capacitor transient with no sustained transmit microphone feed to PA11. Another possible cause is ISR-driven GPIO charge/drive dynamics that settle to constant density. Neither is fully distinguished by this dataset. **Do not conclude we have proven continuous mic->PA11 audio**, despite proven RFIC mic-to-FM TX and VOX paths.

Next investigation should prioritize read-only verification of PA11 analog/logic waveform with a scope **at start and after 200ms**, with the same fixed tone input, and/or an instrumented raw PA11 bit-density/transition-counter in the ISR (requires audited, opt-in base firmware change); correlate it with RFIC AF route register values. Do not just increase mic gain: that was already gain31 and A/B no-write gain31 produced silence after 120ms. If PA11 never carries sustained mic audio, examine whether any documented RFIC TX-to-MCU digital/analog path exists or whether an isolated analog coupling hardware change is needed. Prefer isolated PA-muted bench diagnostics, and keep SOTTO MIC slot30 unchanged. The prior `MIC A/B` report is preserved separately as `mic-ab-live-01.json` and old slot25 signed envelope staged.

## 2026-11-25 PA11 raw-bit density diagnostic (v3 mailbox; firmware update required)

The physical `MIC TIME` trace saved in `mailbox-v1/mic-time-live-01.json` proved a **startup-only PCM signal** that falls to a near-exact zero in 100ms despite continuously high RFIC VOX. This still cannot tell whether there is raw 1-bit activity arriving at PA11 once the reconstructed PCM goes quiet or whether the sigma-delta sampler/PA11 node itself has settled. The current GPIOA11 sampler actively charges and discharges a pin connected to the K6 speaker/RC network, and the currently selected RFIC AF=4 (baseband) route is not a confirmed microphone monitor. Thus both a disconnected/incorrect RFIC analog path and overly aggressive software DC/CIC filtering remain plausible.

**Firmware change**, in `firmware/k6-aura/sotto-api30-pcm-capture.rs` and `sotto-api30-mic.rs.inc`, adds cumulative read-only counters on the **actual one-bit values** sampled on PA11 *before* the CIC/PCM and DC-canceller calculations: (4) number of digital ones sampled, (5) number of transitions between adjacent raw bits, and (6) cumulative absolute changes between per-TIM17-tick counts of ones (a coarse measure of 1kHz envelope activity even if the mean duty-cycle stays ~50%). All use 48 sigma-delta bits per 125us tick at nominal 8kHz. The counters reset at the start of each mic acquisition, exclude the first 3 warm-up ticks to align with PCM indexing, and are served under previously reserved `mic_stream` operations 4/5/6 only while the PA-muted mic lease is active. No RFIC register, GPIO pin write timing, microphone ADC approximation, or existing API30 ring layout is changed. The original ISR still latches a lost/overrun error and cleans up on any missed 125us deadline. The five 32-bit diagnostic state variables are relocated into the existing protected `.ram0` area on the expanded K6 image so main stack headroom remains **2792 bytes**, above the 2780-byte audited floor. Corrected 2026-11-25 firmware build `fw-raw-pa11-v2` size audit: **122,696/122,880 flash bytes** (184 bytes free), **RAM0 3816/3840 bytes** (24 bytes free), module arena 14,976 bytes. The first build failed the stack audit until these counters were moved; **do not flash that first build**.

**New standalone diagnostic module** `MIC RAW`, source `c/k6/mod/sotto_mic_raw.c`, API30, is a drop-in replacement for `MIC TIME` in slot25. It uses the same PA-muted, PTT-gated, 1 second capture with the same RFIC mic routing, and sends a checksummed 33-byte **version3** `MAB1:` UART-mailbox payload before exiting. Version3 has six time windows (`0–20`, `20–40`, `40–80`, `80–120`, `120–200`, `200–1000ms` relative to first valid PCM) and **four measurements per window**: normalized raw 1-bit density, normalized bit-transition fraction, normalized per-tick density change (all 8-bit fractions of the 48-bit oversample stream), and approximate logarithmic 8-bit PCM mean absolute amplitude. It also captures *exact* 16-bit maximum TIM17 ISR cycles used (deadline 12000 cycles), RFIC VOX maximum and total PCM sample count (expected 8000). The v1/v2/v3 formats share the same v0x86 boot-local SRAM mailbox and CRC16, so normal `tools/k6_live.py --port /dev/ttyUSB0 mic-result --save <new.json>` remains compatible. The decoder in `tools/k6_mic_result.py` supports all prior records. The module has no RFIC gain or AF register writes, no FSK packets, and never enables the RF PA.

**Offline verification**: native C harness `tests/data/k6/sotto_mic_raw_host.c` executes the real compiled module on deterministic 8kHz simulated PCM/raw bit-density traces (startup-only, sustained tone, silence, rejected UART mic/PTT), checking that early and late bins reveal the correct transitions, v3 records decode, CRC catches corruption and both mic/TX ownership release properly. The dedicated suite passed 15 tests including v1 and v2 reader regressions; comprehensive focused test run before final v3 metadata tweak passed **35 tests, 5 skipped**. The completed ARM-mode real-firmware Sotto microphone→FSK→receiver mock passed with `pa_restored=true`, PCM ring loss 0 and three recovered packets. These are *offline* checks, not an attended radio raw-bit capture.

**Operator test once the revised firmware and module are installed**: leave radio on dummy load and keep programming cable attached for firmware/slot verification. The MCUs will reboot on firmware flash; existing full SPI backup (`mailbox-v1/preflash-spi-20261125T1903Z.bin`, SHA256 `6a834d2d7f6bd07bd787ea804e7bd47deea4530d426e996adf719b783ad9134d`) protects calibration and library data. Before any flash verify the expected build image and current installed slot25 `MIC TIME` bytes and slot30 `SOTTO MIC` bytes. After flash, test the empty serial mailbox and exact slot25 readback. Then disconnect programming cable, reconnect generator at **1kHz, 0.1V, output ON**, keep dummy load, run `CATALOG → MIC RAW` while holding PTT ~1.5 s. Reconnect programming cable **without rebooting**, return UI to standby, retrieve v3 JSON and compare raw-bit activity and reconstructed PCM across early vs steady-state periods. The module's max-ISR-cycle measurement verifies it stayed within the 12000-cycle TIM17 budget; if the radio latches an overrun it will fail safely and not store misleading completed data.

### PA11 RAW physical firmware + module installation (2026-11-25)

The operator authorized carrying on with PA11 raw-bit research. Source commit `d788ebe7` is the measured MIC RAW firmware implementation. Bench was reserved `flux-pa11-raw-flash`; live radio was TX idle/standby and 7.75V. The previous base firmware build ID was `eb168fb82d789a65`; the installed slot25 `MIC TIME` matched saved signed envelope SHA256 `e0d68f45d8b9258c6d9bab9aa43afacb9032fc65634a29e33945af69f45026b1` and slot30 `SOTTO MIC` matched `791de524e9640967f047f855d92e107405dbe765814f372b6003da5b375386a0`.

**A new full 2,097,152-byte SPI flash backup** was completed, with calibration sector verified by a second read, at `/home/tyler/flux-bench/sotto-mic-calibration-20261125/raw-pa11-v1/preflash-spi-raw-pa11-20261125T1954Z.bin`, SHA256 `f2d0af16e11e249b1d0b1b2e716dab3ca58b76379f3088b4bf726cc9ea0834ad`. The previous whole-device backup from before the MIC A/B mailbox update is also intact.

**Firmware flashed:** staged `raw-pa11-v1/flux-fd6818b-raw-pa11.bin`, SHA256 `1b4dce8975ed8599c6229080230fedd36fc021322800a3e9f398b4a93c53c9a6`, all 120/120 KiB flash blocks completed and radio rebooted itself successfully. Live serial responded immediately after the reboot: new build ID **`5ad086ad63aa3a2a`**, radio standby idle, 7.75V battery, serial results command returned correctly structured `available=false` state. Old module slots survived base firmware update unchanged.

**MIC RAW installed in slot25**, as independent signed API30 module serial `1795636440`, code **2016B**, envelope **2040B**, SHA256 `bb1ceaea2ba532d9930b74163be3cfa662098778e6dde6773ce14632125cc4c6`. Exact SPI readback after installation matched the image; slot30 `SOTTO MIC` remained byte-for-byte unchanged. Cable-connected physical `run-coop 25` loaded module, emitted `MAB1:CLR` to discard stale mailbox, returned **expected `-4`** because headset microphone and UART share jack; no RF transmitted, no freeze. Follow-up read-only live status showed `standby_idle=true`, `transmitting=false`, VFO A 443.500MHz, 7.75V battery; mailbox was empty and the module library listing confirmed slots 25/30 with new build ID. Verification artifacts are in `raw-pa11-v1/firmware-flash.log`, `mic-raw-install-report.json`, `mic-raw-cable-preflight.json`, `final-live-status.json`, and `final-empty-result.json`.

**Now ready for attended generator test** (physical raw bits not yet measured): disconnect program cable and connect 1kHz nominal 0.1V signal generator to microphone pads; enable output; dummy load attached. `CATALOG → MIC RAW`, hold physical PTT at least ~1.5 seconds until `MIC RAW SAVED`, then disconnect generator and reattach serial cable **without cycling radio power**. Return UI to standby and issue `python3 tools/k6_live.py --port /dev/ttyUSB0 mic-result --save /home/tyler/flux-bench/sotto-mic-calibration-20261125/raw-pa11-v1/mic-raw-live-01.json`. v3 report shows raw one-bit density, bit flips, density variation, PCM amplitude in each of six bins, plus maximum ISR cycles and RFIC VOX. The result is boot-local RAM (only latest run, cleared on next attempted MIC test or reboot); the prior v2 trace is already persisted as `mailbox-v1/mic-time-live-01.json`.

Interpretation: if PA11 raw density variation/edge behavior collapses together with PCM after ~100ms, suspect the PA11 speaker/RC node or RFIC AF routing settling to a stable output. If raw PA11 density continues varying, investigate CIC/DC-cancellation math or sampling phase, especially its one-wire charge timing. Raw one-bit density staying at ~50% alone is not proof of sustained audio; watch the per-tick density-variation metric and behavior compared to a generator-OFF control. The first run's RFIC VOX must rise above generator-off baseline to count as a valid tone-on test. The accurate ISR high-water (`isr_peak_cycles`) must stay below 12000; an overrun latches `lost`, causes test failure, and stores no completed record.

### First physical MIC RAW run retrieved via live UART, 2026-11-25 19:59Z

After operator performed the one-second attended nominal 1kHz/0.1V microphone-generator run and reconnected serial cable without reboot, live status showed standby idle, transmitting=false, 7.75V. Read-only mailbox command `mic-result` decoded the real K6's version3 record and saved it permanently to `/home/tyler/flux-bench/sotto-mic-calibration-20261125/raw-pa11-v1/mic-raw-live-01.json`; SHA256 `f2791fe2e07e63904d360e19e2fe4ae13851e5f5550adc25e643d1c69acb1595` below. The mailbox's retrieval UTC time is `2026-11-25T19:59:26.561880+00:00`, not the original physical capture time.

| PA11 bin | One-bit HIGH fraction | Adjacent raw bit transitions | Raw per-tick density variation | mean absolute i16 PCM |
|---|---:|---:|---:|---:|
| 0-20ms | 0.2745 | 0.2627 | 0.0039 | 4352 |
| 20-40ms | 0.8588 | 0.0549 | 0.0000 | 2688 |
| 40-80ms | 0.9961 | 0.0000 | 0.0000 | 88 |
| 80-120ms | 1.0000 | 0.0000 | 0.0000 | 1 |
| 120-200ms | 1.0000 | 0.0000 | 0.0000 | 1 |
| 200-1000ms | 0.9961 | 0.0000 | 0.0000 | 1 |

A stored 0 raw transition fraction means fewer than one reported 8-bit step of 1/255, **not necessarily exactly zero edge events**; 0.9961 near the final bin likewise is a quantized fraction. The combined picture is extremely clear: raw PA11 input samples settle at a very nearly constant HIGH state in 40-80ms and reconstructed PCM becomes near-zero by 80-120ms. This upstream loss of raw bit variation is sufficient to explain the PCM silence; a CIC/DC-canceller bug is **not necessary** to produce it. Time-trace results are consistent with the prior v2 MIC TIME capture, although the magnitude of the initial startup transient differs between runs. The RFIC VOX reached **660**, consistent with microphone tone recognized on the RFIC mic input path; raw counter/PCM values still provide no evidence of steady-state mic sampling to the MCU. The worst ISR ran **6284 cycles**, comfortably under the **12000-cycle** deadline; the ring produced all 8000 samples. No RF transmission was performed.

**Important hardware interpretation:** The known K6 schematic/docs in `docs/k6-stock-firmware.md` and `docs/k6-fd6818-audio.md` associate **PA11 with the MCU TIM1 CH4 PWM voice prompt/speaker output network**, not a confirmed microphone-sample feed. The installed API30 sampler actively charges/discharges PA11 in a one-wire technique. REG_47 selects AF=4 (raw/baseband speaker output); this has not been established to mirror the transmit MICP input onto PA11. The signal dying as a near-logic-HIGH level after a transient is consistent with RC node charging, incorrect analog coupling or PA11 electrical behavior, but the data **do not distinguish a physical node held high from the MCU's active probing/threshold behavior**. Earlier normal-FM audio verified *mic -> RFIC -> FM TX*; this does not prove *mic -> MCU PA11*.

Recommended next validation: perform a **generator-off MIC RAW control** under identical cabling and RFIC route to determine how much of the initial transient is generated by PA11's own RC network, then compare with scope high-impedance observation at PA11 after start (~20ms) versus settled (~200ms), keeping the PTT-gated PA muted. Avoid claiming a digital transmit mic capture route until a sustained raw PA11 modulation is demonstrated. If the pin always settles high even with signal injected, look for an alternate TX mic-to-MCU path or an explicitly isolated external audio interface/hardware modification rather than trying additional gain sweeps.
