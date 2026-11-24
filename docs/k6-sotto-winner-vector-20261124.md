# Sotto best-vector reuse candidate (2026-11-24)

Scope: experimental *source-level* change to the API29 live Sotto TX module,
**not** a change to resident firmware, the radio, KISS provider, or SPI.
Based on `a21765a7` (DCT optimization). The upstream pinned `sotto` submodule
is untouched; `tools/k6_sotto_module.py:adapt` patches the generated transient
module source. No hardware access or RF transmission occurred.

The existing `cb_search` first reads every candidate 20-byte codebook vector,
then `cb_sub` re-reads the winning vector. In singleton searches (stages 1 and 2),
this change retains the 20-byte winner in already allocated, **dead after
centroid selection** coder scratch, and `cb_sub` consumes it instead of issuing
another resource transaction. Stage-0 multi-winner searches are unchanged.
The generated-source adaptation has fail-closed exact-string checks against
the pinned upstream source.

## Measured comparison

Same 1.04-second synthetic input, `--cpi 1.5 --spi-us 34 --adc-cycles 4800`
through actual compiled module -> virtual RF -> compiled RX -> independent
native C and Python wire/session oracles:

| Metric | DCT base `a21765a7` | Winner reuse candidate |
|---|---:|---:|
| Codebook reads while keyed | 4782 | 4679 |
| All resource reads while keyed | 5970 | 5867 |
| Exact recovered codec superframes | 26/26 | 26/26 |
| Exact end-to-end wire session | Yes | Yes |
| Stack high-water | 600 bytes | 600 bytes |
| Module code bytes | 10200 | 10212 |
| Code + 24-byte envelope | 10224 | 10236 |
| Remaining under 10240-byte bundle cap | 16 | 4 |

The shortened final on-screen line `Hold PTT` (previously `Hold PTT to speak`)
recovers the extra 8 bytes needed to stay under the strict size cap while
retaining explicit dummy-load and unplug-cable prompts. Without this change,
code + envelope exceeds the cap by 4 bytes. This leaves little safety margin
for adding other modem features. Given the small ~2.2% reduction in codebook
transactions, **do not integrate without a broader size/performance tradeoff
review**. These are modeled resource transactions, not calibrated physical SPI
measurements. The underlying DCT candidate should remain independent.

Focused run using `FLUX_SOTTO_TREE=/storage5/dev/flux-sotto-reuse-best-20261124`
against the offline harness worktree passed 28/28 tests; the candidate's own
Sotto DCT/module suite passed 14/14. The non-default 1.20-second WAV test also
passed bit-exactly (30/30 codec superframes). These are offline tests only.
