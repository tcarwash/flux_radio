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
