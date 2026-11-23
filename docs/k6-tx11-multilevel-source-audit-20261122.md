# Flux TX-11 — FD6818B native multilevel / DMR TX source audit

Date: 2026-11-22 local / 2026-11-23 UTC.
Worktree: `/storage5/dev/flux-native-fsk-pdm`.
Scope: **read-only inspection of repository source**. No radio,
serial, SPI, RF register writes, flash or TX.

## Research question

Can the B silicon's existing modem take **2-bit symbols or a direct
frequency word** and generate four well-spaced RF frequency states
without the expensive phase/frequency pulse-density trick?

**Finding:** The code presently available in Flux shows working
**binary native-FSK FIFO TX**, and independent **DMR / 4FSK RX DSP**.
It does **not** yet identify a B-specific native 4-FSK TX symbol mode,
DMR TX DAC, transparent digital modulation port, or accessible
multilevel symbol register. This is an **evidence gap, not proof that
the silicon cannot support it**.

## Direct repository evidence

### B-chip source
`firmware/k6-aura/build/aura-fd6818b-port/src/drivers/fd6818b.rs`

- `REG_FSK_BAUD=0x72` is a *baud word* in FSK mode but **tone-2
  frequency** in DTMF mode. It is not evidence of an arbitrary
  baseband sample DAC.
- The raw modem is controlled through
  `REG_FSK_PREAMBLE=0x58`,
  `REG_FSK_CONTROL=0x59`,
  `REG_FSK_FIFO=0x5F`, length/sync related registers 0x5A..0x5D,
  `REG_WORK_MODE=0x3F` and `REG_FSK_BAUD=0x72`.
- The B driver defines `FSK_CONTROL_BASE=0x0028`,
  RX enable 0x1000, TX enable 0x0800 and FIFO-clear bits, along
  with an 8-word `FSK_FRAME_LEN` for its ordinary packet service.
  These named operations are **byte/word-oriented framing and FIFO**
  controls, not a documented multilevel symbol alphabet.
- The driver separately uses `REG_TONE_GAIN` and frequency words
  for DTMF / Bell-202 AFSK. This is a **separate FM-audio tone
  oscillator**, physically exercised in TX-15. Two tone generators
  do not by themselves prove quadrature or multilevel direct-RF TX.
- A repository search of the B driver found no named `4fsk`,
  `dpmr` or native-DMR **transmitter** path. Absence of
  documentation or symbol names is not an exhaustive chip
  capability result.

### Receive-only evidence must not be promoted to TX

- `c/k6/ifrx.c`, `c/k6/ifrx_tables.h` and
  `c/k6/ifrx_tables_4h.h` implement **4-FSK receive template**
  filters. They operate on GP0 captured receiver samples, not a
  programmable B-chip multilevel transmit DAC.
- `c/k6/mod/dfskcap.c` and its DMR variants test **plain FSK RX**
  slicing / sync detection on real DMR signals. Its REG_58[7:6]
  configurations and variable REG_72 RX clocks show useful
  receiver configurability, **not demonstrated 4-FSK TX**.
- `c/k6/mod/dmrdet.c`, `c/k6/dmrsync.h` and related code perform
  **receive-only** DMR sync detection/decoding through the GP0 IF
  path. This does not imply that the RFIC generates DMR.

### Physical TX observations already established elsewhere

- Native 2-FSK FIFO bitstream and frame boundaries are physically
  demonstrated; PDM eight-level *information* was recovered.
- [FSK-24 real RF IQ holdout](k6-fsk-physical-iq-holdout-20261122.md)
  finds ~107–171% heldout full-complex EVM against independent ideal
  instantaneous binary FSK. Better *native modulator physics* could
  help, but the multi-level solution is unproven.
- [TX-12 MICIN bypass lab](k6-micin-bypass-lab.md) found
  a B-driver-level path to disable TX voice filters and opt-in
  MICIN-on state `REG_30=0xC1FE`; it has not yet established
  proper physical 4-FSK transmission or jack-level injection.

## Three separate hypotheses, not one

1. **Native multilevel RF FSK mode:** an undocumented 4-symbol
   B-specific FIFO or mode switch generates direct multi-deviation
   RF. Needs actual B-specific manufacturer guidance, stock image
   disassembly, or reversible observed behavior; **no known working
   register candidate** in present source.
2. **Analog shaped 4-FSK MICIN mode:** existing analog FM modulator
   receives shaped four-level instantaneous-frequency **voltage**.
   Requires electrical mapping, conditioning, flat TX voice path,
   bias/HPF characterization and physical RF spectrum verification.
   This need not be a native four-level FIFO feature.
3. **Digital baseband/DMR bypass port:** a separate synchronous chip
   data input may exist, potentially accepting samples or 4-FSK
   levels. Needs B-chip pin mapping and K6 board-level connectivity
   before any firmware reconfiguration. Older FD6818/0x4819
   guides must not be treated as an FD6818B/0x4829 register map.

## Discriminating next research (priority order)

1. **Read-only B-specific documentation and comparison:** search
   versioned BK4829/FD6818B datasheets and stock B board drivers for
   TX baseband mode, field width, symbol DAC or PCM pin semantics.
   Record exact source/hardware revision; do not infer semantics from
   original non-B FD6818.
2. **Read-only B stock firmware audit:** identify all writes to
   0x58,0x59,0x5A..0x5F,0x70..0x72 and the 0x3F work-mode register,
   especially any TX paths outside binary packets. Distinguish
   voice/DTMF/FSK and startup defaults from unknown functions.
3. **Board topology:** trace the RFIC MICIN, TCXO modulation,
   digital baseband pads and VRAMP/APC connections using verified
   matching K6 board revision and component markings.
4. If and only if a specific multilevel mode emerges, formulate
   an inert emulator/source test with an explicit 00/01/10/11
   sequence and independently recover four measured frequency
   levels on short, conducted RF **after** full safety checks.
   Do not brute-force unknown B registers on live TX.
5. Continue separate reversible MICIN flat-4FSK and tone-control
   investigations regardless of the outcome.

## Decision

**TX-11 status:** no verified native 4-FSK B-chip TX path. Retain
TX-11 as an independent research route, and prioritize TX-12 MICIN
electrical validation as the known plausible 4-FSK waveform path.

This audit changes neither firmware nor experimental calibration and
makes **no** claim that direct multilevel TX is impossible.
