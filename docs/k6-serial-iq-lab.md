# Live IF / IQ serial acquisition lab

The opt-in serial lab now captures the FD6818B GP0 output on PB1 using DMA
while the MCU downconverts it to signed 8-bit complex I/Q and sends binary frames.
This is **software-derived receive I/Q**, not a native I/Q transceiver interface.
The board's battery/microphone ADC channels are not repurposed or treated as RF ADCs.
No transmit operation is involved.

## Hardware results

Tested on the attached FD6818B Flux radio and Prolific programming cable:

| Test | UART | Measured output | Result |
|---|---:|---:|---|
| Raw GP0 population-count snapshot | 500000 | 2048 real counts, about 89.5 kcounts/s | Passed; about 23 ms acquisition, export after DMA stops |
| Continuous IQ, CIC ratio 8 | 500000 | 11,565 complex samples/s, 23.1 kB/s payload | Three 10-second captures passed, 346,962 complex samples total |
| Continuous IQ, CIC ratio 32 | 115200 | 2,900 complex samples/s | Three seconds passed |
| Deliberate serial overload, ratio 8 | 115200 | About 11.6 kcomplex samples/s generated | Failed as expected: 113 output frames dropped in two seconds; missing samples detected |
| Higher-rate IQ, ratio 4 | 550000 | About 23.0 kcomplex samples/s generated | Not clean: nine output frames dropped in three seconds |

The final ratio-8 runs had **zero DMA overruns, uncertain DMA laps, DMA transfer
errors, UART errors, response CRC errors, output drops, sample-index gaps, or
nonmonotonic timestamps**. Each run delivered 115,654 complex samples. Maximum
polling pause was about 177 microseconds, with at most about 1.1 KiB pending in an
8 KiB DMA ring. This establishes acquisition and transport continuity for those runs.

A seven-channel receive survey found 162.550 MHz stronger than the adjacent
channels: approximately -74 to -77 dBm versus -83 to -85 dBm. Its raw GP0 counts
also had greater AC power. Longer tests used that channel and an 8 kHz mixer.
The source identity and modulation were not independently verified; no on-air
packet decode or calibrated test-tone result is claimed.

The earlier polling-only UART experiment dropped 25 frames at ratio 4 / 550000.
Interrupt-driven UART service reduced that to nine, but did not make this rate
lossless. Keep ratio 8 / 500000 as the validated continuous configuration.

## Build and run

Build/install the experimental image using the process in
[the serial throughput guide](k6-serial-throughput.md), with `K6_SERIAL_LAB=1`,
Data and Scan enabled. The lab image remains within the 122880-byte application
limit. Normal builds exclude its commands, acquisition code and UART IRQ hook.
Backups and experimental outputs belong outside Git.

Short raw capture, followed by a ten-second continuous I/Q capture:

```sh
python3 tools/k6_iq_capture.py --port /dev/ttyUSB0 --raw --ms 100 \
  --hz 162550000 --mix 8000 --output serial-test-output/raw

python3 tools/k6_iq_capture.py --port /dev/ttyUSB0 --ms 10000 \
  --ratio 8 --baud 500000 --hz 162550000 --mix 8000 \
  --output serial-test-output/iq
```

Each command writes a JSON measurement report and compressed NPZ capture. The
NPZ `samples` array contains interleaved signed int8 I/Q (or real signed int8
population counts for `--raw`). Its `frames` array contains columns: sequence,
first source index, end timestamp, cycles-per-DMA-observation Q8, sample count,
mode, ratio. JSON `passed` requires the loss/error counters and continuity checks
to be clean. Failed captures remain available; spectral estimates are omitted when
missing samples would make a concatenated FFT misleading.

A capture is bounded to ten seconds. Raw mode stops after 2048 counts; `--ms` is
its upper time limit, not a requested continuous raw-stream duration. The radio
returns to 115200 after each capture, then restarts at session end.

## Acquisition and timing

`c/k6/serial_if_lab.c` reserves DMA channel 1 and TIM17 only when unused, routes
GPIOB IDR bytes into an 8192-byte circular arena buffer, and counts PB1 ones in
64 observations. RF setup and route readback are owned by the cable host.
RF registers, DMA/timer clocks and resource enable state are cleaned up afterward.

DMA runs flat out on this chip: TIM17 does **not** establish a precise sampling
cadence. The measured roughly 5.9 million DMA observations/s are repeated GPIO
reads, **not a demonstrated 5.9 MS/s ADC conversion clock**. Both observation rate
and output rate vary with CPU/bus activity. Use measured rates and timestamps;
do not label the stream as exactly 9600, 11025 or 12000 samples/s.

The mixer uses measured-time phase estimates and a 16-phase cosine table. Five
fractional bits are retained through the existing third-order CIC before int8
quantization. Decimation supports powers of two through 32. Frame timestamps
refer to the estimated final contributing source observation, before correcting
CIC group delay; they are not a calibrated RF phase reference. The population
count boxcar, CIC droop, mixer harmonics, source timing and alias rejection still
need RF/DSP qualification before declaring a particular digital mode supported.

UART TX uses two independently framed buffers, drained by the existing UART
interrupt. Queue publication uses short critical sections; acquisition does not
wait for each UART byte. Sequence/source-index gaps and explicit queue-drop
counts expose overload instead of filling holes silently. DMA loop lag and long
polling pauses are also recorded, so unseen laps cannot simply be called clean.

## Experimental wire extension

Commands use the existing A6 framing. Queue `baud FAST`, capture, and `baud 115200`
at baseline speed as described in the throughput guide.

- Request `0x70`: 16 bytes, frequency u32 LE, maximum milliseconds u32 LE, mixer Hz
  u32 LE, mode byte (0 raw / 1 IQ), ratio byte, two reserved zero bytes.
- Samples `0x69`: sequence in the frame address. Payload begins with source index
  u32 LE, end timestamp in 96 MHz cycles u32 LE, measured cycles/observation Q8
  u32 LE, mode byte, ratio byte, two reserved zero bytes. Up to 240 data bytes
  follow (120 complex samples). Raw mode uses stride 64; IQ uses stride 64*ratio.
- Final `0x70`: 24 u32 LE statistics, followed by receive frequency, mixer frequency,
  RSSI register, IF selector, bandwidth register, and GP0 route readback. Field
  names are in `tools/k6_iq_capture.py`. Values representing signed status or raw
  count extrema/sum must be interpreted as signed 32-bit integers.

Raw timestamps are reconstructed from measured rate after acquisition. IQ timestamps
are estimated during acquisition. Neither format is the transport-independent
`K6IQ` host/simulator prototype; this A6 extension is specifically a cable lab.

## Verification

Twenty targeted tests pass across the capture parser, loss detection, existing
portable C mixer/CIC, generic IQ framing/reference frontend, and serial benchmark.
Both opt-in and normal firmware configurations build. The physical overload test
confirms that actual output loss produces a failed report even with clean UART CRCs.
