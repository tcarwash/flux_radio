# K6 software emulator

## Goal

Run the same ARM firmware image built for the KD32F328, then provide a virtual K6 around it:
keypad events in, display and serial output out, and deterministic models for persistent flash
and the FD6818B radio chip. This is separate from the existing K6 module and signal emulators,
which execute individual modules or DSP paths but do not boot Aura.

## Current milestone: execute the real image

The prototype boots the current Aura/Blowfang image from its reset vector under a Cortex-M0 engine
and has been run through early initialization into the application loop. It models enough polled
peripheral behavior for that path to progress and reports MMIO addresses that still use generic
register storage/default reads. This is evidence that the image executes; it is not yet evidence
that the whole radio behaves correctly.

The live runner has been exercised with a local radio SPI backup and the VFO module overlaid in
virtual slot 30. This confirms the bundle can be signed and exposed to the firmware's library scan;
module execution and UI behavior still need interactive comparison. An LLM-oriented debugging
workflow, including machine-readable freeze reports, is documented in
[k6-emulator-llm-debug.md](k6-emulator-llm-debug.md). Next work is to implement interrupt delivery
and improve timing, ADC, RF/audio, and UI behavior in small observable slices.
Save stable access traces as fixtures after comparison; don't bless traces from synthetic defaults
as hardware truth.

The application image starts at `0x08002000`; SRAM is `0x20000000..0x20006000`, with Aura's
application RAM beginning at `0x20001028`. The PAC's KD32F328 SVD is the source for peripheral
register addresses. The checked-in firmware build and linker map are the source for the image,
vector table, and RAM reservations.

The runnable emulator is `sim/k6emu`: it starts the actual raw firmware image under
Unicorn's Cortex-M0 engine, models clock-ready bits, the keypad matrix, polled USART1, SPI1 NOR
flash, SPI2 LCD traffic, timer reads, ADC channel inputs, NVIC enable/pending/priority registers,
and the FD6818B's bit-banged register bus. Timer delays advance virtual time at a coarse
instruction quantum. `--live` opens a Tk window with a live LCD view and keyboard controls. The
emulator has partial interrupt support and an optional behavioral RF receive model. Treat its outputs as a
development model, not as a replacement for radio testing.

## Remote browser workbench

Use `python3 -m sim.k6ide` for the existing browser workbench. It defaults to
`0.0.0.0:5056`, so it can be followed from another device at
`http://<host-LAN-IP>:5056`. It has no login or token and is intended for the trusted home LAN;
do not expose it to the public internet. Keep this workbench synchronized with changes to the
simulator and app UI.

Use `--spi-image sim/k6emu/runs/k6-spi.bin` to persist virtual SPI between restarts. That ignored
local file can contain calibration data, so do not add it to Git. The workbench does not open the
radio programming cable; emulator execution remains separate from hardware and RF validation.

## SDR-backed receive model (experimental)

Flux adapts the MIT-licensed [SoapyFang receiver model](https://github.com/baitisj/blowfang/tree/4a924aec08c1ee41487f0642820dc1c757f96ecf/sim/soapyfang)
from Blowfang to the K6's actual emulated three-wire register bus. The default
emulator is unchanged; enable the model explicitly at process startup.

```sh
python -m pip install -r requirements-dev.txt
# Synthetic stations; no SDR or radio required.
python -m sim.k6ide --host 127.0.0.1 --image PATH/firmware.bin --rf-source synthetic
# Replay a single-frequency SigMF recording (once by default).
python -m sim.k6ide --host 127.0.0.1 --image PATH/firmware.bin --rf-source sigmf:PATH/capture.sigmf-meta
# Replay a complex NumPy capture with its actual recorded metadata.
python -m sim.k6ide --host 127.0.0.1 --image PATH/firmware.bin \
  --rf-source npy:PATH/capture.npy --rf-rate 240000 --rf-center 440850000
# Live RX only: install the OS SoapySDR Python bindings and RTL-SDR driver first.
python -m sim.k6ide --host 127.0.0.1 --image PATH/firmware.bin \
  --rf-source soapy:driver=rtlsdr --rf-gain 30
```

The same RF arguments work with `python -m sim.k6emu PATH/firmware.bin` and
`--report-json`. Add your existing **virtual** SPI seed with `--spi-image` (browser)
or `--spi-flash` (headless) if the firmware requires installed modules/settings.
The browser panel reports source, tuned frequency, IQ center, model RSSI/noise/squelch
and errors, plus a user-initiated snapshot of the last five seconds of model FM audio.
The snapshot is not the firmware's actual speaker output. Replay EOF is shown;
`--rf-loop` explicitly repeats a recording. SigMF supports single-frequency
`cf32_le`, `ci16_le`, and `ci8`; NPY requires a one-dimensional complex array and
explicit sample rate and center frequency. The `npy-u8:PATH` source additionally supports our archived interleaved unsigned
8-bit RTL-SDR I/Q arrays with explicit rate and effective center. All replay formats
are memory-mapped.
Sample rates must be 48 kS/s–20 MS/s. The example NPY rate/center are illustrative;
use the actual capture metadata. Keep off-air recordings and calibration files out of Git.

The synthetic band contains FM test tones at 146.520, 146.550, 440.850 and 440.900 MHz.
Tuning uses REG38/39 in 10 Hz units; the model provides RSSI (REG67), noise (REG65),
glitch (REG63) and the squelch bit (REG0C). Each 16-bit bus read is latched once.
Other registers retain their previous behavior. Live SDR uses only RX streams;
PTT and all RFIC writes remain virtual, never control a physical transmitter.
A live source is explicitly opened on startup and closed on reboot/exit.
Use an available SDR, not a shared bench device already leased by another experiment.
The browser server is unauthenticated: keep it on localhost when replaying private
recordings or receiving live audio.

**Evidence boundary:** RSSI uses the inherited +174 dB scale; chip filters, AGC,
noise, squelch and FM audio are behavioral estimates, not silicon measurements.
`--rf-dbm-offset` supplies an operator calibration reference for recorded/live IQ;
it does not certify the model. Recorded/synthetic sources advance with virtual
time; live SDR uses wall time and approximate pacing. Retuning and very long
virtual delays can skip samples; no cycle-accurate RF timing is promised.
Native direct 4800-baud FSK FIFO reception is supported as described below.
GP0/PA11 integration, other modem PHYs, and TX waveform generation are **not**
modeled by this backend. Existing synthetic
DMA/IF injection is separate and unchanged. Real SDR use requires a subsequent
device-specific check; CI uses generated IQ and a mocked Soapy RX device.

Run the offline regression suite with
`python -m pytest -q tests/test_k6_rf_backend.py`. The upstream model/DSP
are attributed in source; the Flux adapter, NPY/SigMF replay, register latch,
workbench UI and tests are maintained here.

### Native FSK replay and module testing

The optional RF backend now feeds the native direct 4800-baud 2FSK receive
model from the channel discriminator **before** the speaker low-pass filter.
It acquires the 16-byte 0x55 preamble and programmable four-byte sync from
IQ, chooses symbol phase and discriminator polarity, and delivers little-endian
16-bit words at the nominal bit clock. The model never uses expected payload
bytes, CRC, or known frame times to acquire or repair a frame.

Supported register configuration: REG58=00e1, REG72=c194, REG5C=5625 (hardware
CRC disabled), REG59=00f8 base (no scrambling or inversion), even byte count
in REG5D, and REG5E low bits 1–7 as the RX threshold. REG59 RX clear/enable
arms one frame; REG02/0C model interrupt acknowledgement/pending with REG3F
masking; REG0B reports modeled sync polarity; REG5F pops one word per bus
transaction. The modeled eight-word FIFO drops new words on overflow, returns
zero on underflow, and raises threshold-crossing, sync, and frame-done events.
A receiver must drain and rearm. This is an explicit behavioral contract,
not a claim that real silicon uses these exact overflow, latch, polarity or
timing semantics. Unsupported configurations are reported rather than decoded.
The clock is fixed nominal 4800 baud, not an adaptive clock-recovery loop;
long virtual gaps and retunes discard partial frames. The workbench exposes
frame, FIFO, overflow and unsupported-configuration diagnostics.

The existing Cortex-M0 module harness (`tests/k6_live_emu.py`) accepts an
optional virtual `rf_backend` and `rf_hz` for `fd_read`, `fd_write`, `tune` and
`rx_hz`. The default GP0/DMA fixture path remains unchanged. An emulator-only
C polling module exercises the real module API and stores raw received frames
in scratch; run it with:

```sh
python tools/k6_fsk_emulator_replay.py --source npy-u8:PATH/capture.npy \
  --rate 1024000 --center 433780500 --tune 433500000 --duration 2.5
```

The example center includes the known receiver frequency correction for our
November 23 bench archives; use actual capture metadata for other files.
The tool prints frame counts, independent software L64 CRC checks, hashes,
first words, stack use and FIFO diagnostics. It does not open a physical
device. It requires the existing Cortex-M0 module build tools (clang, lld,
llvm-objcopy, llvm-nm). See [archived replay evidence](k6-fsk-emulator-replay-20261124.md).
The ordinary RF backend remains receive-only. A separate offline virtual TX
backend now supports finite preloaded direct 4800-baud bursts. It does not
implement hardware CRC, FFSK, 1200/2400-baud direct FSK, DMA/GP0 or a complete
production Sotto receiver.

### Offline virtual link and parameter sweeps

Run two compiled Cortex-M0 module fixtures against a shared virtual RF
timeline, with no physical SDR or radio:

```sh
python tools/k6_fsk_virtual_link.py --packets 4 \
  --threshold 4,6 --poll-us 1000,5000,10000 --gap-us 0,12000 \
  --seed 1,2 --csv sweep.csv --summary-json ranked.json
```

Sweep deviation, carrier offset, C/N, clock error, FIFO threshold, polling
interval, interpacket gap, initial timing phase and explicit leading-word
fault injection. CSV/JSON include exact packet delivery, CRC, raw bit
errors, FIFO diagnostics and timing. Ranked summaries use worst-case
delivery across tested seeds and start phases. The same RX fixture can
replay physical archives with `--frame-bytes`, `--threshold`, `--poll-us`.
See [virtual-link workflow and initial sweeps](k6-fsk-virtual-link-20261124.md).
This is one-way timestamped replay, not a live duplex network or a
calibrated silicon model.

## Test a library module

`--module-bundle` signs each selected module with the same key used by the firmware and places the
result in the virtual SPI library at its assigned slot. The input bundle files are not modified,
and the physical radio is never contacted. Use an SPI backup as the base so settings and fonts are
present, then overlay a new or changed module:

```sh
python3 -m sim.k6emu \
  firmware/k6-aura/out-fd6818b/flux-fd6818b.bin \
  --live \
  --spi-flash $HOME/flux-backups/k6-spiflash-emulator.bin \
  --module-bundle firmware/k6-aura/out-fd6818b/vfo-module-bundle \
  --module-slots 30 \
  --key-file $HOME/.config/flux/k6-mod.key
```

Press `M` or Enter to open the module list, navigate to the module, then press Enter to run it.
`--module-slots` is optional; omitting it overlays every module in the bundle. For a different
module, point `--module-bundle` at the release bundle containing it and select its manifest slot.
The key stays on this machine. Any external flash writes by the simulated firmware affect only its
in-memory copy unless `--save-spi` is explicitly supplied.

Install and start the emulator with the current keyed Aura/Blowfang image (from the repository
root):

```sh
python3 -m pip install -r sim/k6emu/requirements.txt
python3 -m sim.k6emu \
  firmware/k6-aura/out-fd6818b/flux-fd6818b.bin \
  --live \
  --spi-flash $HOME/flux-backups/k6-spiflash-emulator.bin \
  --adc 0=2048 --adc 1=3000 \
  --lcd-trace /tmp/k6-lcd.jsonl
python3 -m sim.k6emu.lcd /tmp/k6-lcd.jsonl -o /tmp/k6-screen.svg
```

In the live window, use digits, `*`, and `#` for keypad keys; `M` or Enter for Menu; Escape for
Exit; arrow keys for Up/Down; `V` for V/M; `A` for A/B; `B` for Band; F1/F2 for side keys; and hold
Space to press PTT. Close the window to stop and print the emulator summary. For repeatable runs,
`--key-event` schedules keypad presses/releases and `--gpio-event` drives a GPIO (for example,
`1000:A:10:low` presses PTT and `1500:A:10:high` releases it). These actions exercise firmware
state only; physical RF output is never produced. The optional RF backend below models receiver audio. `--adc 0=RAW` sets VOX/mic channel
0, and `--adc 1=RAW` sets battery channel 1 (raw values are 0..4095; defaults are 2048). Without
an SPI backup, external flash starts erased (`0xFF`); settings, font data, and factory calibration
will therefore be synthetic. Headless runs still write an SVG LCD snapshot after execution.

## Models and evidence

- MCU instruction execution and exception behavior: Cortex-M0 engine.
- MCU peripherals: KD32F328 register models, progressing from boot-critical RCC/FLASH/GPIO and
  USART to DMA, timers, SPI/I2C, ADC, and interrupt behavior as the firmware reaches them.
- Screen and keypad: virtual display plus timestamped key events.
- SPI flash: an image-backed 2 MB model, seeded from a radio backup when available; keep a
  user's calibration backup outside Git.
- FD6818B: begin with deterministic register and FIFO behavior from firmware drivers and
  captured traces; label unmeasured RF behavior as a model rather than hardware equivalence.

The existing module harnesses in `tests/` and `tests/k6_live_emu.py` remain useful lower-level
fixtures. They are not proof that the complete Aura application boots in an emulated board.

## Capture needed

An external SPI-flash backup gives the emulator the radio's persistent layout and calibration
bytes. Capture it read-only after the radio is back at its normal Aura screen or Blowfang
EXPLORE/arena screen:

```sh
mkdir -p $HOME/flux-backups
python3 tools/k6_install.py \
  --port /dev/ttyUSB0 backup \
  -o $HOME/flux-backups/k6-spiflash-emulator.bin
```

The backup command only reads the radio's external SPI flash; it does not reflash firmware. First
get the radio back to its normal running screen (remove/reseat the battery if it is still stuck in
a cable session), connect the cable, and substitute the current `/dev/ttyUSB*` path if it differs.
The file contains factory calibration. Keep it local and do not commit or share it. No `--key-file`
is needed for this backup.
