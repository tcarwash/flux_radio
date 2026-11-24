# Flux Sotto — first selectable live-TX bench image

This branch offers an **opt-in development image** for the FD6818B K6 and
a separately installable module. It is **not** a finished Sotto transceiver.

## Included / not included

* Balanced RAM (14,400-byte arena), packed KUL2, live serial module installation,
  remote LCD/keypad/PTT, Base + Data services, TIM14 independent TX watchdog.
* API 27 read-only SPI codebook/deck lookup, API 28 asynchronous 33-word
  4,800-baud native-FSK start/poll/abort, API 29 TIM17 8-kHz four-block PA11
  PCM capture, and opt-in Sotto mode integrated with the normal VFO control.
* The **SOTTO TX** module contains the pinned Sotto slim encoder, bounded PCM
  ring, 4-slot FDV1 packet stream, rollover and explicit abort behavior.
  Codebook and deck are stored separately in SPI.
* When installed, cycle VFO modes past CWF to **SOTTO**. Physical PTT launches
  the installed module. Each press is a live streaming session: packets can go
  on air while PTT remains held; release drains the bounded queue.
* There is **no Sotto receiver/decoder or speaker playback yet**. Ordinary
  analog FM reception remains active outside a TX session. The UI is an
  experimental **transmit-only mode**.
* Serial IQ is deliberately opt-in and not enabled in this image. Designer and
  Scan services are excluded from this small bench profile; the installed
  theme/modules remain in SPI but may be unavailable until a matching profile
  is reflashed. No notification LED driver is enabled. The observed
  20%-peak software PWM breathing is documented separately; autonomous TIM16
  interrupts require independent hardware qualification of vector ownership.

## Bench-only safety limits

* Actual TX is never triggered by flashing/installing a module. A **physical
  PTT press** is required after selecting the Sotto VFO mode. No CAT/serial
  PTT launches it. The module's menu entry also requires physical PTT.
* Before radio testing use an appropriate **dummy load**, a clean air-coupled
  SDR/receiver, and a suitable amateur-frequency simplex channel. Unplug the
  programming cable before PTT: PA10 doubles as its receive conductor.
* The initial bench session is limited to **20 seconds** of capture/drain.
  The guarded TX lease lasts at most 25 s with independent watchdog backup.
  PA remains off between guarded packet bursts and when the module opens a
  session. Any overflow, cancellation, timer collision, timeout or bad
  SPI resource aborts and returns to receive.
* TIM17 and SRAM interrupt-vector mapping are acquired only during TX.
  TX watchdog retains ownership of the mapping. The sampler refuses TIM17
  clock/IRQ already in use; failure must not overwrite another modem.
* Before a physical flash: capture and retain a **full 2 MiB SPI backup**,
  verify the target B chip, and review the current radio's SPI resource
  allocations. Do not flash this small bench profile over an active
  four-pack configuration expecting every feature to remain available.
* This is the first *flashable test candidate*, not a production release.
  Real RF timing, microphone audio quality and overrun margin must be
  measured on the radio before promoting it.

## Reproducible build / installation (manual, never automatic)

From the isolated worktree `/storage5/dev/flux-sotto-live-mode`:

```sh
# Flash a prebuilt image only after reviewing the profile and backup.
python3 tools/k6_install.py --port /dev/ttyUSB0 backup -o /storage5/dev/flux-radio-backups/sotto-preflash-spi.bin
python3 tools/k6_install.py --port /dev/ttyUSB0 flash firmware/k6-aura/out-fd6818b/flux-fd6818b.bin
# If your existing key requires it, supply --key-file as before.

# Build separately installable, TX-capable module + K6SC deck.
python3 tools/k6_sotto_module.py --host-resources --live-tx --out /tmp/sotto-bench-module

# This is a read-only plan. It refuses missing/mismatched K6CB at 0x100000.
python3 tools/k6_sotto_live_install.py /tmp/sotto-bench-module --port /dev/ttyUSB0

# This explicitly permits installing a TX-capable module; it does NOT transmit.
python3 tools/k6_sotto_live_install.py /tmp/sotto-bench-module --port /dev/ttyUSB0 \
    --install --accept-experimental-tx
```

Sotto's K6CB codebook is a pinned, 30,752-byte separate resource. The
installer does **not** overwrite protected SPI locations to provision it.
The module cannot be used until K6CB's full CRC matches the required digest.
A failed preflight is a stop condition, not a reason to write arbitrary flash.

## Emulator verification boundary

Tests cover: relocation and bit-exact codec output; bounded four-slot queues
and epoch rollover; failure and overflow cleanup; compiled firmware API 28
completion/timeout/chip rejection; TIM17 capture lifecycle, IRQ/vector cleanup
and timer-busy refusal; installed API 29 module encoding and six completed
FDV1 packets through the simulated RFIC while PTT is held. Additional VFO
selector verification belongs in `tests/test_k6_sotto_vfo_sim.py`.

These are compiled-Cortex-M0/electrical-peripheral **models**, not OTA
reception or oscilloscope evidence. Before calling this a usable digital
voice radio, implement and independently verify the loadable RX demodulator,
Sotto decoder, speaker playback and both-sided interoperability.
