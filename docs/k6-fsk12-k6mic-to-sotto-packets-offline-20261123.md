# Flux FSK-12 — real K6 microphone capture to Sotto-1200 L64 packets (offline)

Date: 2026-11-23. Research branch `research/offline-sotto-fsk-dv-20261123`.

**Result: PASS for offline recorded K6-microphone -> 8kHz PCM -> fixed-point Sotto 1200 -> native-FSK-size Flux L64 CRC packets -> packet decode/reassembly -> host Sotto PCM playback.** This is *not* an RF or live on-radio Sotto encoding test. The other Flux RF agent has resumed; exclusive shared serial-port hardware lease is not claimed by this task. No RF, flash, TX, SDR, PTT or serial operation was performed while building this pipeline. Canonical hardware lock remains mandatory for any later physical RF test and a queue ACK will be requested before scheduling it.

## Previously validated K6 microphone recording

The independently completed K6 microphone capture resides in `/home/tyler/flux-bench/micvoice-20261123/`, produced by the separate `research/mic-digital-voice` thread. Its **already recorded** waveform is read-only; source audio and encoded voice are **not included in Git** and are kept only under the local bench area. From `micvoice-capture.json`: **8,164 PCM samples**, measured ADC sample rate **7,956.3395 Hz**, duration **1.0261 s**, **0 clipping events**. The `micvoice-speech-band.wav` file is the speech-band-filtered version (mono s16), SHA256 `720d51205240eca70cca9bddfa6cc2ef938f641dabb213162aa791c21fcb3b60`. The unfiltered original is preserved separately, as is an independent successfully captured speaker-tone fixture. Mic capture source report comes from the other thread and is not modified.

## Newly implemented offline conversion and exact packet validation

Script: `tools/k6_sotto_k6mic_offline.py`.

1. Read the **precise measured ADC sample rate** from the capture metadata rather than treating its rounded integer WAV rate as exact. Confirm sample count, mono signed 16-bit PCM and that ADC clipping was not reported.
2. Polyphase resample the existing speech-band waveform to **8,000 samples/s** with a bounded rational approximation to the measured rate. Preserve sample timing and amplitude without further AGC. A 40-ms Sotto 1200 superframe requires **320 signed 16-bit PCM samples**. Zero-pad only the final partial frame (**111 additional samples**) so the output becomes **8,320 samples / 1.040 s**.
3. Encode using the *existing* native **host Sotto fixed-point encoder** (`sotto_enc_fx -1200`, binary SHA256 `98878e7f9c91550c5ad18de4d4e57b10b5c21d02d24e5794c0aa811147e66162`). This yields **26 genuine six-byte Sotto 1200 superframes / 156 bytes**.
4. Pack with the existing `pager.profile('L','1200',depth=1,parity=False,short_last=True)`, **four 64-byte whitened/CRC16 packets** (sequence 0–3, message ID 93 in this local fixture). No manual protocol reinterpretation, custom CRC or format conversion.
5. Immediately unpack and CRC-check every transmitted-sized packet, reconstruct all 26 original compressed frames using `pager.Assembler`, and verify **byte-for-byte equality** with the 156 encoder-output bytes. The host Sotto fixed-point decoder (`sotto_dec_fx -1200`) then decodes those CRC-accepted recovered bits to **8,320 signed 16-bit 8-kHz PCM samples**. A listenable WAV is produced. Audio PCM is *lossy* with respect to the source; exact equality applies only to the Sotto compressed bits after packet transport/reassembly.
6. An additional ideal-packet scheduling model uses the physically measured K6 native-FSK L64 restart. For this short test it estimates **~0.604s total finite-packet air occupancy**, with nominal 160ms playout buffer and ~611ms model capture-to-playout delay, **zero simulated packet loss**. These are model outputs, **not fresh RF timing measurements or demonstrated microphone-to-speaker latency**.

### Observed local offline result

| Measurement | Result |
| --- | ---: |
| Microphone source | K6 native PA11/ADPCM previously captured |
| Source duration and sample rate | 1.0261 s at 7956.3395 Hz |
| Source clipping events | 0 |
| Resampled/zero-padded PCM | 8320 samples / 1.040 s at 8kHz |
| Sotto codec | Sotto 1200 fixed-point, 40ms / 6 bytes |
| Genuine compressed Sotto frames | **26 / 26** |
| Actual generated L64 packets | **4** (256 total bytes) |
| CRC16-valid L64 packets | **4 / 4** |
| Reassembled compressed bits | **156 / 156 bytes exact** |
| Host PCM decoded from packets | **8320 samples, 1.040s** |
| Ideal-model L64 finite-packet RF time | ~0.604s |
| Physical RF or new hardware operations | **None** |

Locally saved output folder: `/home/tyler/flux-bench/micvoice-20261123/sotto-offline-run01/`.

| Artifact | SHA256 |
| --- | --- |
| `k6mic-resampled-8k.raw` | `1f91f4191f63dd189ddb49dc89f86faece86f1e1eea68459daafd1f7eede49ca` |
| `k6mic-sotto1200.bit` | `60c8478cf6da655a62dd2317104d79ea30a6f655f73545fb152dd87d49a59ad3` |
| `k6mic-l64-packets.bin` | `d81956bf1ae57747eaf8353edd2d10453b530408a61e781600ee8c7dc05d8bd6` |
| `k6mic-sotto-rx-reassembled.bit` | `60c8478cf6da655a62dd2317104d79ea30a6f655f73545fb152dd87d49a59ad3` |
| `k6mic-received-8k.wav` | `097aa1bed7eafa1ed4d462d67398ae4346edb3f329a0414aeb2f0c81b00e190f` |

The JSON manifest `k6mic-sotto-offline-report.json` has the pinned sample/codec hashes, clip rate/sample count, all encoded/reassembly checks, simulated transport model, and local artifact names.

Read-only rerun (new destination; refuses overwrite):

```bash
python3 tools/k6_sotto_k6mic_offline.py \
  --capture /home/tyler/flux-bench/micvoice-20261123/micvoice-speech-band.wav \
  --meta /home/tyler/flux-bench/micvoice-20261123/micvoice-capture.json \
  --encoder /storage5/dev/flux-mic-digital-voice/sotto/c/fixed/sotto_enc_fx \
  --decoder /storage5/dev/flux-mic-digital-voice/sotto/c/fixed/sotto_dec_fx \
  --out /tmp/k6mic-sotto-verify --msg-id 93

python3 -m pytest -q tests/test_k6_sotto_k6mic_offline.py
```

## Next goals, coordinated with other agent

- **RF microphone-derived packet validation** is the logical next modest hardware test: reuse known-safe finite-frame K6 native-FSK module and one small immutable packet set from this real microphone recording, with an independently demodulating SDR receiver and exact CRC validation. This needs a **new prospectively frozen RF fixture, queue ACK and exclusive bench lease** before any physical RF; no reservation has been made yet.
- Move toward *live* microphone-to-FSK voice by combining the proven K6 microphone ADPCM/PA11 acquisition with a resident or module-side Sotto encoder, strictly bounded acquisition buffers and nonblocking transport queue. The current capture consumes a cable-disconnect UI sequence and is only about one second; it does not prove that capture + native FSK transmission can run simultaneously or that the codec fits MCU scheduling.
- For continuous transmission, [FSK FIFO issue #9](https://github.com/tcarwash/flux_radio/issues/9) has one successful **single** in-flight write; multiple-batch tests still show unexplained `0x00F8` and bit slips. Root cause tracked under [#15](https://github.com/tcarwash/flux_radio/issues/15). Avoid speculative refill until source/waveform audit.
- Improve real-world robustness with timed reception, CRC gating, one-loss interleave/parity recovery, jitter buffer and concealment. The strong clean two/eight-packet RF experiments used **prerecorded external Sotto sources**, whereas this milestone uses **actual radio microphone audio but no RF**. Their combination is not yet an experimentally complete live voice system.

**Status:** offline microphone-to-actual packet-format transport is verified. Keep the other agent's physical RF research uninterrupted.
