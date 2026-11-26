# GP0 microphone / DMA / PCM physical measurement records (2026-11-25)

The eight checked-in JSON documents below are **byte-identical copies** of the
recorded boot-local `MAB1` measurement reports on the operator's development
workstation. The metadata was inspected before publishing; only device-test
metrics and controlled tone ON/OFF descriptions are present. No SPI flash
backup, firmware/module signing key, raw microphone audio, full SDR IQ,
identifying personal data or arbitrary binary firmware is included.

**Do not promote these reports to a complete Sotto voice qualification.**
The short v6/v8 captures establish input-frequency-correlated GP0→MCU PCM;
the v10 uninterrupted DMA IRQ test used a **synthetic 2ms per block workload**,
not the actual Sotto codec and RFIC FIFO concurrently. Recorded
`retrieved_at_utc` timestamps are readout times, not necessarily the
physical time when stimulus was applied. Voltage units of the nominal
test generator were not completely established; these are relative metrics.

| File | Mailbox type / version | SHA256 of original JSON |
| --- | --- | --- |
| [`gp0-dma-v1-tone-1khz-01.json`](gp0-dma-v1-tone-1khz-01.json) | gp0_dma_tx_mic_spectrum, v6 | `7e27fca6df4df42d1e21cc4a74694e82c57b31061418baf793d4f4c0366784b5` |
| [`gp0-dma-v1-tone-2khz-01.json`](gp0-dma-v1-tone-2khz-01.json) | gp0_dma_tx_mic_spectrum, v6 | `a9cdae6b66a3332bd5a8482bc9e3171d9acfa199697d1f8ba5ba384a78eaf758` |
| [`gp0-dma-v1-tone-off-01.json`](gp0-dma-v1-tone-off-01.json) | gp0_dma_tx_mic_spectrum, v6 | `4938e5a8552dd5938c3e44e95ed60e079901d809f9c30dd6ea41d6652bfdd4a9` |
| [`gp0-pcm-v1-tone-1khz-01.json`](gp0-pcm-v1-tone-1khz-01.json) | gp0_dma_mic_8khz_pcm, v8 | `16178dbf1798b1896ac3ec7898eff647d5fc7f0ccbc3a2b1aafc7e9469f603f0` |
| [`gp0-pcm-v1-tone-2khz-01.json`](gp0-pcm-v1-tone-2khz-01.json) | gp0_dma_mic_8khz_pcm, v8 | `bc2ec8cc48c1b055e9f7e47234db34057fba2ecad259544e7f922c3e63351191` |
| [`gp0-pcm-v1-tone-off-01.json`](gp0-pcm-v1-tone-off-01.json) | gp0_dma_mic_8khz_pcm, v8 | `d87fbb970f98fa997a91fc1bdb45dbb56907066e1981a31141c025a63ba6524b` |
| [`gp0-irq-v1-tone-2khz-01.json`](gp0-irq-v1-tone-2khz-01.json) | gp0_dma_irq_8khz_pcm_stream, v10 | `158dcc4928f3eb8b2e41c171275828b93c54adc5d05454bc5ef8c0cf37363259` |
| [`gp0-irq-v1-tone-2khz-02.json`](gp0-irq-v1-tone-2khz-02.json) | gp0_dma_irq_8khz_pcm_stream, v10 | `15bba9b4d59b99effa754929655a8b670c54ea88d9de57a2061b33384b00d1a7` |

[Integration assessment](../../K6_SOTTO_ON_RADIO_TECHNICAL_DOSSIER.md)
· [full DMA spectrum report](../../k6-sotto-gp0-mic-dma-20261125.md)
· [physical PCM report](../../k6-sotto-gp0-mic-pcm-20261125.md)
· [continuous IRQ report](../../k6-sotto-gp0-dma-irq-20261125.md).
