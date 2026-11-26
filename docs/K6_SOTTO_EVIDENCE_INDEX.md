# K6 on-radio Sotto: exhaustive repository evidence index

**Snapshot: 2026-11-25 PST.** [Start with the integrated technical dossier](K6_SOTTO_ON_RADIO_TECHNICAL_DOSSIER.md). This auto-generated manifest links the detailed, immutable-in-Git Markdown research logs, including historical failures as well as passes. A document's existence does **not** mean the experiment was physically run or is suitable to flash. Consult its own qualification status. The index is deliberately broad because microphone, GP0, RFIC FSK, FIFO and SoapyFang findings interlock.

## Experiment, research and integration logs

| Primary document | Title in repo | Notes |
| --- | --- | --- |
| [`k6-fd6818-audio.md`](k6-fd6818-audio.md) | What the K6's MCU can see of the FD6818's audio | RFIC B-chip hardware |
| [`k6-fd6818b-refcal.md`](k6-fd6818b-refcal.md) | FD6818B reference calibration and APRS IF experiments | RFIC B-chip hardware |
| [`k6-frequency-burst-fifo-20261124.md`](k6-frequency-burst-fifo-20261124.md) | Frequency modem: blind-start acquisition and FD6818B FIFO receive replay | RFIC FSK/FIFO evidence |
| [`k6-frequency-modem-soapyfang-20261124.md`](k6-frequency-modem-soapyfang-20261124.md) | K6 frequency-modem offline lab: FIFO FSK vs register-paced subaudio/4-FSK | Virtual radio lab |
| [`k6-fsk-emulator-replay-20261124.md`](k6-fsk-emulator-replay-20261124.md) | Native FSK emulator: archived RF replay, 2026-11-24 | RFIC FSK/FIFO evidence |
| [`k6-fsk-virtual-link-20261124.md`](k6-fsk-virtual-link-20261124.md) | Virtual native-FSK link and parameter sweeps — 2026-11-24 | RFIC FSK/FIFO evidence |
| [`k6-gp0-startup-phase-diagnostic-20261125.md`](k6-gp0-startup-phase-diagnostic-20261125.md) | GP0 startup-phase hardware diagnostic (2026-11-25 PST) | Mic/GP0 audio capture |
| [`k6-gp0-uart-probe-20261125.md`](k6-gp0-uart-probe-20261125.md) | Sotto GP0 live-serial diagnostic follow-up (2026-11-25 PST) | Mic/GP0 audio capture |
| [`k6-if-modem.md`](k6-if-modem.md) | K6 softmodem from the IF: a study | Supporting modem/RF research |
| [`k6-if-streaming-wspriq.md`](k6-if-streaming-wspriq.md) | IF capture/streaming: lessons from WSPR IQ | Supporting modem/RF research |
| [`k6-native-fsk-burst-clock-parity-20261124.md`](k6-native-fsk-burst-clock-parity-20261124.md) | Native FSK: consecutive-burst rearm, sample-clock recovery and CRC/parity erasures | RFIC FSK/FIFO evidence |
| [`k6-own-fsk.md`](k6-own-fsk.md) | K6: transmitting FSK the MCU writes itself | RFIC FSK/FIFO evidence |
| [`k6-packet-mailbox-v1.md`](k6-packet-mailbox-v1.md) | K6 Data-pack packet-provider mailbox — experimental API27 (not enabled by default) | Supporting modem/RF research |
| [`k6-serial-iq-lab.md`](k6-serial-iq-lab.md) | Live IF / IQ serial acquisition lab | Supporting modem/RF research |
| [`k6-soapyfang-modulation-validation-20261124.md`](k6-soapyfang-modulation-validation-20261124.md) | SoapyFang RX model × five modulation references — offline validation | Virtual radio lab |
| [`k6-soapyfang-sotto-lab-20261124.md`](k6-soapyfang-sotto-lab-20261124.md) | SoapyFang Sotto-1200 half-duplex voice lab | Sotto architecture/codec/API/hardware work |
| [`k6-sotto-api29-native-mock-20261125.md`](k6-sotto-api29-native-mock-20261125.md) | Native API29 FSK callback: register/FIFO fault injections | Sotto architecture/codec/API/hardware work |
| [`k6-sotto-api31-physical-known-packet-20261125.md`](k6-sotto-api31-physical-known-packet-20261125.md) | API31 known-Sotto packet: physical RF test (2026-11-25) | Sotto architecture/codec/API/hardware work |
| [`k6-sotto-api31-rx-development-20261124.md`](k6-sotto-api31-rx-development-20261124.md) | Native K6 FD6818B 4,800-baud Sotto RX: isolated API31 development gate | Sotto architecture/codec/API/hardware work |
| [`k6-sotto-end-to-end-mock-20261124.md`](k6-sotto-end-to-end-mock-20261124.md) | Sotto-1200 end-to-end mock voice link | Sotto architecture/codec/API/hardware work |
| [`k6-sotto-firmware-dev.md`](k6-sotto-firmware-dev.md) | Flux Sotto-1200 development firmware (FD6818B) | Sotto architecture/codec/API/hardware work |
| [`k6-sotto-gp0-dma-irq-20261125.md`](k6-sotto-gp0-dma-irq-20261125.md) | K6 microphone: preemptible DMA/8kHz PCM producer for Sotto | Sotto architecture/codec/API/hardware work |
| [`k6-sotto-gp0-mic-dma-20261125.md`](k6-sotto-gp0-mic-dma-20261125.md) | Sotto mic-to-MCU: GP0 selector12 5.5MS/s DMA spectrum probe (2026-11-25) | Sotto architecture/codec/API/hardware work |
| [`k6-sotto-gp0-mic-pcm-20261125.md`](k6-sotto-gp0-mic-pcm-20261125.md) | K6 standalone microphone GP0 → 8kHz PCM physical diagnostic | Sotto architecture/codec/API/hardware work |
| [`k6-sotto-half-duplex-design-20261124.md`](k6-sotto-half-duplex-design-20261124.md) | Flux K6 Sotto digital voice: half-duplex architecture | Sotto architecture/codec/API/hardware work |
| [`k6-sotto-live-arm-e2e-20261124.md`](k6-sotto-live-arm-e2e-20261124.md) | Sotto live ARM microphone -> FSK -> virtual IQ, 2026-11-24 | Sotto architecture/codec/API/hardware work |
| [`k6-sotto-live-serial-bench-20261125.md`](k6-sotto-live-serial-bench-20261125.md) | 2026-11-25 live-serial K6 Sotto 1 kHz bench session | Sotto architecture/codec/API/hardware work |
| [`k6-sotto-live-serial-tone-20261125.md`](k6-sotto-live-serial-tone-20261125.md) | Sotto live-serial 1 kHz tone probe | Sotto architecture/codec/API/hardware work |
| [`k6-sotto-mic-e2e-handoff-20261125.md`](k6-sotto-mic-e2e-handoff-20261125.md) | Sotto live microphone end-to-end RF handoff (2026-11-25) | Sotto architecture/codec/API/hardware work |
| [`k6-sotto-mic-route-experiments-20261125.md`](k6-sotto-mic-route-experiments-20261125.md) | K6 microphone-to-MCU route probe — AF bypass and GP0/PB1, without opening PCB | Sotto architecture/codec/API/hardware work |
| [`k6-sotto-mock-qualification-20261124.md`](k6-sotto-mock-qualification-20261124.md) | Sotto no-hardware mock qualification | Sotto architecture/codec/API/hardware work |
| [`k6-sotto-modem-module.md`](k6-sotto-modem-module.md) | Sotto as a loadable Flux modem | Sotto architecture/codec/API/hardware work |
| [`k6-sotto-optimization-20261124.md`](k6-sotto-optimization-20261124.md) | Offline Sotto Modem Optimization — measured DCT candidate | Sotto architecture/codec/API/hardware work |
| [`k6-sotto-rf-qualification-goal-20261125.md`](k6-sotto-rf-qualification-goal-20261125.md) | Goal: live RF Sotto TX/RX qualification, API31 (2026-11-25) | Sotto architecture/codec/API/hardware work |
| [`k6-sotto-tail-timing-offline-20261124.md`](k6-sotto-tail-timing-offline-20261124.md) | Sotto live API29 33-word FIFO: offline tail-timing differential | Sotto architecture/codec/API/hardware work |
| [`k6-sotto-two-way-flashable-gates-20261124.md`](k6-sotto-two-way-flashable-gates-20261124.md) | Sotto half-duplex **flashable** end state: integration and bench gates | Sotto architecture/codec/API/hardware work |
| [`k6-sotto-virtual-link-20261124.md`](k6-sotto-virtual-link-20261124.md) | Actual live Sotto module over virtual RF — 2026-11-24 | Sotto architecture/codec/API/hardware work |
| [`upstream-iq-serial-backport.md`](upstream-iq-serial-backport.md) | Flux: IQ streaming and high-speed serial — upstream backport guide | Supporting modem/RF research |

## Source, test and tool inventory

The table below lists **repository files**, not just links to the experimental prose. This inventory includes source in `firmware/k6-aura`, C modules, reusable emulation tools and tests. It excludes generated build output, downloads and private on-air IQ/PCM captures. The fully versioned source remains in this branch; files referenced only from other local worktrees are described in the dossier, not silently claimed present here.

### Firmware overlays and core API implementation (22 files)

- [`firmware/k6-aura/apply-configurable-fsk.py`](../firmware/k6-aura/apply-configurable-fsk.py)
- [`firmware/k6-aura/apply-fd6818b-afsk-module-api.py`](../firmware/k6-aura/apply-fd6818b-afsk-module-api.py)
- [`firmware/k6-aura/apply-fd6818b-fsk-tx-api.py`](../firmware/k6-aura/apply-fd6818b-fsk-tx-api.py)
- [`firmware/k6-aura/apply-live-serial.py`](../firmware/k6-aura/apply-live-serial.py)
- [`firmware/k6-aura/apply-mic-result-serial.py`](../firmware/k6-aura/apply-mic-result-serial.py)
- [`firmware/k6-aura/apply-serial-lab.py`](../firmware/k6-aura/apply-serial-lab.py)
- [`firmware/k6-aura/apply-sotto-api28-resources.py`](../firmware/k6-aura/apply-sotto-api28-resources.py)
- [`firmware/k6-aura/apply-sotto-api29-async-fsk.py`](../firmware/k6-aura/apply-sotto-api29-async-fsk.py)
- [`firmware/k6-aura/apply-sotto-api30-mic.py`](../firmware/k6-aura/apply-sotto-api30-mic.py)
- [`firmware/k6-aura/apply-sotto-installed-vfo-mode.py`](../firmware/k6-aura/apply-sotto-installed-vfo-mode.py)
- [`firmware/k6-aura/apply-sotto-rx4800.py`](../firmware/k6-aura/apply-sotto-rx4800.py)
- [`firmware/k6-aura/apply-sotto-service.py`](../firmware/k6-aura/apply-sotto-service.py)
- [`firmware/k6-aura/aura-fd6818b-fsk-api.patch`](../firmware/k6-aura/aura-fd6818b-fsk-api.patch)
- [`firmware/k6-aura/build-sotto-data.sh`](../firmware/k6-aura/build-sotto-data.sh)
- [`firmware/k6-aura/build-sotto.sh`](../firmware/k6-aura/build-sotto.sh)
- [`firmware/k6-aura/live-serial.rs`](../firmware/k6-aura/live-serial.rs)
- [`firmware/k6-aura/sotto-api28-resources.rs.inc`](../firmware/k6-aura/sotto-api28-resources.rs.inc)
- [`firmware/k6-aura/sotto-api29-async-fsk.rs.inc`](../firmware/k6-aura/sotto-api29-async-fsk.rs.inc)
- [`firmware/k6-aura/sotto-api30-gp0-capture.rs`](../firmware/k6-aura/sotto-api30-gp0-capture.rs)
- [`firmware/k6-aura/sotto-api30-mic.rs.inc`](../firmware/k6-aura/sotto-api30-mic.rs.inc)
- [`firmware/k6-aura/sotto-api30-pcm-capture.rs`](../firmware/k6-aura/sotto-api30-pcm-capture.rs)
- [`firmware/k6-aura/sotto-fsk4800-rx.rs.inc`](../firmware/k6-aura/sotto-fsk4800-rx.rs.inc)

### C modules and codec adapters (86 files)

- [`c/k6/k6fsk_ref.py`](../c/k6/k6fsk_ref.py)
- [`c/k6/mod/acarsfsk.c`](../c/k6/mod/acarsfsk.c)
- [`c/k6/mod/acarsfsk_am.c`](../c/k6/mod/acarsfsk_am.c)
- [`c/k6/mod/aprsdfsk.c`](../c/k6/mod/aprsdfsk.c)
- [`c/k6/mod/aprsdfsk_440.c`](../c/k6/mod/aprsdfsk_440.c)
- [`c/k6/mod/aprsfsk.c`](../c/k6/mod/aprsfsk.c)
- [`c/k6/mod/aprsfsk_440.c`](../c/k6/mod/aprsfsk_440.c)
- [`c/k6/mod/aprsfskb.c`](../c/k6/mod/aprsfskb.c)
- [`c/k6/mod/aprsfskb_440.c`](../c/k6/mod/aprsfskb_440.c)
- [`c/k6/mod/aprsfskd_440.c`](../c/k6/mod/aprsfskd_440.c)
- [`c/k6/mod/aprsfsks_440.c`](../c/k6/mod/aprsfsks_440.c)
- [`c/k6/mod/dfskcap.c`](../c/k6/mod/dfskcap.c)
- [`c/k6/mod/dfskcap_acars.c`](../c/k6/mod/dfskcap_acars.c)
- [`c/k6/mod/dfskcap_acars6.c`](../c/k6/mod/dfskcap_acars6.c)
- [`c/k6/mod/dfskcap_acars6am.c`](../c/k6/mod/dfskcap_acars6am.c)
- [`c/k6/mod/dfskcap_acarsb.c`](../c/k6/mod/dfskcap_acarsb.c)
- [`c/k6/mod/dfskcap_acarsc.c`](../c/k6/mod/dfskcap_acarsc.c)
- [`c/k6/mod/dfskcap_acarsd.c`](../c/k6/mod/dfskcap_acarsd.c)
- [`c/k6/mod/dfskcap_acarse.c`](../c/k6/mod/dfskcap_acarse.c)
- [`c/k6/mod/dfskcap_dmr.c`](../c/k6/mod/dfskcap_dmr.c)
- [`c/k6/mod/dfskcap_dmr2.c`](../c/k6/mod/dfskcap_dmr2.c)
- [`c/k6/mod/dfskcap_dmr3.c`](../c/k6/mod/dfskcap_dmr3.c)
- [`c/k6/mod/dfskcap_dmr4.c`](../c/k6/mod/dfskcap_dmr4.c)
- [`c/k6/mod/dfskcap_dmr5.c`](../c/k6/mod/dfskcap_dmr5.c)
- [`c/k6/mod/dv_live_slim_dct.h`](../c/k6/mod/dv_live_slim_dct.h)
- [`c/k6/mod/fskaprs.c`](../c/k6/mod/fskaprs.c)
- [`c/k6/mod/fskbcap.c`](../c/k6/mod/fskbcap.c)
- [`c/k6/mod/gp0cap.c`](../c/k6/mod/gp0cap.c)
- [`c/k6/mod/gp0cap_afc.c`](../c/k6/mod/gp0cap_afc.c)
- [`c/k6/mod/gp0dma.c`](../c/k6/mod/gp0dma.c)
- [`c/k6/mod/gp0fm.c`](../c/k6/mod/gp0fm.c)
- [`c/k6/mod/gp0raw.c`](../c/k6/mod/gp0raw.c)
- [`c/k6/mod/gp0rx.h`](../c/k6/mod/gp0rx.h)
- [`c/k6/mod/gp0sweep.c`](../c/k6/mod/gp0sweep.c)
- [`c/k6/mod/gp0tone.c`](../c/k6/mod/gp0tone.c)
- [`c/k6/mod/iflive.c`](../c/k6/mod/iflive.c)
- [`c/k6/mod/iflive_ffsk.c`](../c/k6/mod/iflive_ffsk.c)
- [`c/k6/mod/iflive_fsweep.c`](../c/k6/mod/iflive_fsweep.c)
- [`c/k6/mod/iflive_g2.c`](../c/k6/mod/iflive_g2.c)
- [`c/k6/mod/iflive_h1200.c`](../c/k6/mod/iflive_h1200.c)
- [`c/k6/mod/iflive_h1350.c`](../c/k6/mod/iflive_h1350.c)
- [`c/k6/mod/iflive_h1440.c`](../c/k6/mod/iflive_h1440.c)
- [`c/k6/mod/iflive_h1500.c`](../c/k6/mod/iflive_h1500.c)
- [`c/k6/mod/iflive_h1600.c`](../c/k6/mod/iflive_h1600.c)
- [`c/k6/mod/iflive_k6t.c`](../c/k6/mod/iflive_k6t.c)
- [`c/k6/mod/iflive_tsweep.c`](../c/k6/mod/iflive_tsweep.c)
- [`c/k6/mod/live_cancel_demo.c`](../c/k6/mod/live_cancel_demo.c)
- [`c/k6/mod/rffsklab.c`](../c/k6/mod/rffsklab.c)
- [`c/k6/mod/rffskrx.c`](../c/k6/mod/rffskrx.c)
- [`c/k6/mod/rffsktx.c`](../c/k6/mod/rffsktx.c)
- [`c/k6/mod/sotto_data_tx.h`](../c/k6/mod/sotto_data_tx.h)
- [`c/k6/mod/sotto_fsk_drain_tx.c`](../c/k6/mod/sotto_fsk_drain_tx.c)
- [`c/k6/mod/sotto_gp0_irq_capture.h`](../c/k6/mod/sotto_gp0_irq_capture.h)
- [`c/k6/mod/sotto_gp0_irq_tx.c`](../c/k6/mod/sotto_gp0_irq_tx.c)
- [`c/k6/mod/sotto_gp0_pcm.h`](../c/k6/mod/sotto_gp0_pcm.h)
- [`c/k6/mod/sotto_guard_tx.h`](../c/k6/mod/sotto_guard_tx.h)
- [`c/k6/mod/sotto_live_engine.h`](../c/k6/mod/sotto_live_engine.h)
- [`c/k6/mod/sotto_live_session.h`](../c/k6/mod/sotto_live_session.h)
- [`c/k6/mod/sotto_live_tx.c`](../c/k6/mod/sotto_live_tx.c)
- [`c/k6/mod/sotto_m0rt.h`](../c/k6/mod/sotto_m0rt.h)
- [`c/k6/mod/sotto_mic_ab.c`](../c/k6/mod/sotto_mic_ab.c)
- [`c/k6/mod/sotto_mic_cal.c`](../c/k6/mod/sotto_mic_cal.c)
- [`c/k6/mod/sotto_mic_cal_math.inc`](../c/k6/mod/sotto_mic_cal_math.inc)
- [`c/k6/mod/sotto_mic_gain_sweep.c`](../c/k6/mod/sotto_mic_gain_sweep.c)
- [`c/k6/mod/sotto_mic_gp0_dma.c`](../c/k6/mod/sotto_mic_gp0_dma.c)
- [`c/k6/mod/sotto_mic_gp0_irq.c`](../c/k6/mod/sotto_mic_gp0_irq.c)
- [`c/k6/mod/sotto_mic_gp0_pcm.c`](../c/k6/mod/sotto_mic_gp0_pcm.c)
- [`c/k6/mod/sotto_mic_raw.c`](../c/k6/mod/sotto_mic_raw.c)
- [`c/k6/mod/sotto_mic_routes.c`](../c/k6/mod/sotto_mic_routes.c)
- [`c/k6/mod/sotto_mic_time.c`](../c/k6/mod/sotto_mic_time.c)
- [`c/k6/mod/sotto_tone_bits.h`](../c/k6/mod/sotto_tone_bits.h)
- [`c/k6/mod/sotto_tone_fixture_tx.c`](../c/k6/mod/sotto_tone_fixture_tx.c)
- [`c/k6/mod/sotto_tone_pcm.h`](../c/k6/mod/sotto_tone_pcm.h)
- [`c/k6/mod/sotto_tone_tx.c`](../c/k6/mod/sotto_tone_tx.c)
- [`c/k6/mod/sotto_tx_codec_io.h`](../c/k6/mod/sotto_tx_codec_io.h)
- [`c/k6/mod/sotto_tx_fixture.c`](../c/k6/mod/sotto_tx_fixture.c)
- [`c/k6/mod/sotto_tx_fixture.inc`](../c/k6/mod/sotto_tx_fixture.inc)
- [`c/k6/mod/sotto_tx_selftest.c`](../c/k6/mod/sotto_tx_selftest.c)
- [`c/k6/mod/sotto_tx_stage.h`](../c/k6/mod/sotto_tx_stage.h)
- [`c/k6/mod/sotto_txlease_probe.c`](../c/k6/mod/sotto_txlease_probe.c)
- [`c/k6/serial_if_lab.c`](../c/k6/serial_if_lab.c)
- [`c/k6/sotto_dv_wire.c`](../c/k6/sotto_dv_wire.c)
- [`c/k6/sotto_dv_wire.h`](../c/k6/sotto_dv_wire.h)
- [`c/k6/sotto_fx_oa_fast.c`](../c/k6/sotto_fx_oa_fast.c)
- [`c/k6/sotto_pager.c`](../c/k6/sotto_pager.c)
- [`c/k6/sotto_pager.h`](../c/k6/sotto_pager.h)

### Emulator, bench, replay and build tools (44 files)

- [`tools/k6_frequency_fifo_replay.py`](../tools/k6_frequency_fifo_replay.py)
- [`tools/k6_frequency_modem_lab.py`](../tools/k6_frequency_modem_lab.py)
- [`tools/k6_fsk_boundary_bench.py`](../tools/k6_fsk_boundary_bench.py)
- [`tools/k6_fsk_burst_recovery.py`](../tools/k6_fsk_burst_recovery.py)
- [`tools/k6_fsk_emulator_replay.py`](../tools/k6_fsk_emulator_replay.py)
- [`tools/k6_fsk_iq_edges.py`](../tools/k6_fsk_iq_edges.py)
- [`tools/k6_fsk_rf_record.py`](../tools/k6_fsk_rf_record.py)
- [`tools/k6_fsk_sotto_sync_rx.py`](../tools/k6_fsk_sotto_sync_rx.py)
- [`tools/k6_fsk_virtual_link.py`](../tools/k6_fsk_virtual_link.py)
- [`tools/k6_gp0.py`](../tools/k6_gp0.py)
- [`tools/k6_live.py`](../tools/k6_live.py)
- [`tools/k6_live_acceptance.py`](../tools/k6_live_acceptance.py)
- [`tools/k6_live_bench.py`](../tools/k6_live_bench.py)
- [`tools/k6_live_v2.py`](../tools/k6_live_v2.py)
- [`tools/k6_mic_result.py`](../tools/k6_mic_result.py)
- [`tools/k6_pcmhash.py`](../tools/k6_pcmhash.py)
- [`tools/k6_serial_bench.py`](../tools/k6_serial_bench.py)
- [`tools/k6_sotto_api28_resource_gate.py`](../tools/k6_sotto_api28_resource_gate.py)
- [`tools/k6_sotto_api29_async_gate.py`](../tools/k6_sotto_api29_async_gate.py)
- [`tools/k6_sotto_api30_mic_gate.py`](../tools/k6_sotto_api30_mic_gate.py)
- [`tools/k6_sotto_e2e_mock.py`](../tools/k6_sotto_e2e_mock.py)
- [`tools/k6_sotto_firmware_audit.py`](../tools/k6_sotto_firmware_audit.py)
- [`tools/k6_sotto_firmware_emu.py`](../tools/k6_sotto_firmware_emu.py)
- [`tools/k6_sotto_gp0_irq_arm_gate.py`](../tools/k6_sotto_gp0_irq_arm_gate.py)
- [`tools/k6_sotto_gp0_route_arm_gate.py`](../tools/k6_sotto_gp0_route_arm_gate.py)
- [`tools/k6_sotto_installed_vfo.py`](../tools/k6_sotto_installed_vfo.py)
- [`tools/k6_sotto_ivf_deck.py`](../tools/k6_sotto_ivf_deck.py)
- [`tools/k6_sotto_live_arm_gate.py`](../tools/k6_sotto_live_arm_gate.py)
- [`tools/k6_sotto_mock_qualify.py`](../tools/k6_sotto_mock_qualify.py)
- [`tools/k6_sotto_module.py`](../tools/k6_sotto_module.py)
- [`tools/k6_sotto_opt_bench.py`](../tools/k6_sotto_opt_bench.py)
- [`tools/k6_sotto_pcm_rf_build.py`](../tools/k6_sotto_pcm_rf_build.py)
- [`tools/k6_sotto_rf_bench.py`](../tools/k6_sotto_rf_bench.py)
- [`tools/k6_sotto_rf_bench_packed.py`](../tools/k6_sotto_rf_bench_packed.py)
- [`tools/k6_sotto_rf_compare_old_sdr.py`](../tools/k6_sotto_rf_compare_old_sdr.py)
- [`tools/k6_sotto_rf_goal_bench.py`](../tools/k6_sotto_rf_goal_bench.py)
- [`tools/k6_sotto_rf_raw_tone.py`](../tools/k6_sotto_rf_raw_tone.py)
- [`tools/k6_sotto_rx4800_dispatch_gate.py`](../tools/k6_sotto_rx4800_dispatch_gate.py)
- [`tools/k6_sotto_rx4800_firmware_gate.py`](../tools/k6_sotto_rx4800_firmware_gate.py)
- [`tools/k6_sotto_rx_stream.py`](../tools/k6_sotto_rx_stream.py)
- [`tools/k6_sotto_tail_timing.py`](../tools/k6_sotto_tail_timing.py)
- [`tools/k6_sotto_tone_live.py`](../tools/k6_sotto_tone_live.py)
- [`tools/k6_sotto_virtual_link.py`](../tools/k6_sotto_virtual_link.py)
- [`tools/k6_store_templates/static/radio-live.js`](../tools/k6_store_templates/static/radio-live.js)

### Regression and ARM tests (106 files)

- [`tests/c/sotto8k_enc_twice.c`](../tests/c/sotto8k_enc_twice.c)
- [`tests/data/k6/dfskcap-20261110.log.gz`](../tests/data/k6/dfskcap-20261110.log.gz)
- [`tests/data/k6/dfskcap-dmr4-runM-20261111.log.gz`](../tests/data/k6/dfskcap-dmr4-runM-20261111.log.gz)
- [`tests/data/k6/dfskcap-dmr5-runN-20261112.log.gz`](../tests/data/k6/dfskcap-dmr5-runN-20261112.log.gz)
- [`tests/data/k6/dfskcap-pre-20261110.log.gz`](../tests/data/k6/dfskcap-pre-20261110.log.gz)
- [`tests/data/k6/fsk_rx_emu.c`](../tests/data/k6/fsk_rx_emu.c)
- [`tests/data/k6/fsk_tx_emu.c`](../tests/data/k6/fsk_tx_emu.c)
- [`tests/data/k6/gp0afcoff-20261107.log.gz`](../tests/data/k6/gp0afcoff-20261107.log.gz)
- [`tests/data/k6/gp0cap-20261106.log.gz`](../tests/data/k6/gp0cap-20261106.log.gz)
- [`tests/data/k6/gp0carrier-20261107.log.gz`](../tests/data/k6/gp0carrier-20261107.log.gz)
- [`tests/data/k6/gp0lvl-0.07-20261107.log.gz`](../tests/data/k6/gp0lvl-0.07-20261107.log.gz)
- [`tests/data/k6/gp0lvl-0.12-20261107.log.gz`](../tests/data/k6/gp0lvl-0.12-20261107.log.gz)
- [`tests/data/k6/gp0lvl-0.18-20261107.log.gz`](../tests/data/k6/gp0lvl-0.18-20261107.log.gz)
- [`tests/data/k6/iflive-20261107.log.gz`](../tests/data/k6/iflive-20261107.log.gz)
- [`tests/data/k6/sotto_api29_native_host.rs`](../tests/data/k6/sotto_api29_native_host.rs)
- [`tests/data/k6/sotto_api29_pa_muted_20261124.rs.inc`](../tests/data/k6/sotto_api29_pa_muted_20261124.rs.inc)
- [`tests/data/k6/sotto_async29_tx_call_emu.c`](../tests/data/k6/sotto_async29_tx_call_emu.c)
- [`tests/data/k6/sotto_codec_resource_crc_probe.c`](../tests/data/k6/sotto_codec_resource_crc_probe.c)
- [`tests/data/k6/sotto_gp0_irq_capture_host.c`](../tests/data/k6/sotto_gp0_irq_capture_host.c)
- [`tests/data/k6/sotto_gp0_pcm_host.c`](../tests/data/k6/sotto_gp0_pcm_host.c)
- [`tests/data/k6/sotto_live_packet_parity.c`](../tests/data/k6/sotto_live_packet_parity.c)
- [`tests/data/k6/sotto_mic30_call_emu.c`](../tests/data/k6/sotto_mic30_call_emu.c)
- [`tests/data/k6/sotto_mic_ab_host.c`](../tests/data/k6/sotto_mic_ab_host.c)
- [`tests/data/k6/sotto_mic_gain_sweep_host.c`](../tests/data/k6/sotto_mic_gain_sweep_host.c)
- [`tests/data/k6/sotto_mic_gp0_dma_host.c`](../tests/data/k6/sotto_mic_gp0_dma_host.c)
- [`tests/data/k6/sotto_mic_gp0_irq_host.c`](../tests/data/k6/sotto_mic_gp0_irq_host.c)
- [`tests/data/k6/sotto_mic_gp0_pcm_host.c`](../tests/data/k6/sotto_mic_gp0_pcm_host.c)
- [`tests/data/k6/sotto_mic_gp0_route_call_emu.c`](../tests/data/k6/sotto_mic_gp0_route_call_emu.c)
- [`tests/data/k6/sotto_mic_raw_host.c`](../tests/data/k6/sotto_mic_raw_host.c)
- [`tests/data/k6/sotto_mic_routes_host.c`](../tests/data/k6/sotto_mic_routes_host.c)
- [`tests/data/k6/sotto_mic_time_host.c`](../tests/data/k6/sotto_mic_time_host.c)
- [`tests/data/k6/sotto_resources31_call_emu.c`](../tests/data/k6/sotto_resources31_call_emu.c)
- [`tests/data/k6/sotto_rx31_call_emu.c`](../tests/data/k6/sotto_rx31_call_emu.c)
- [`tests/data/k6/sotto_rx4800_native_host.rs`](../tests/data/k6/sotto_rx4800_native_host.rs)
- [`tests/data/k6/sotto_wire_rx_emu.c`](../tests/data/k6/sotto_wire_rx_emu.c)
- [`tests/k6_fd6818b_fsk.py`](../tests/k6_fd6818b_fsk.py)
- [`tests/k6_live_emu.py`](../tests/k6_live_emu.py)
- [`tests/test_fsk_rx.py`](../tests/test_fsk_rx.py)
- [`tests/test_k6_fd6818b_fsk.py`](../tests/test_k6_fd6818b_fsk.py)
- [`tests/test_k6_frequency_fifo_replay.py`](../tests/test_k6_frequency_fifo_replay.py)
- [`tests/test_k6_frequency_modem_lab.py`](../tests/test_k6_frequency_modem_lab.py)
- [`tests/test_k6_fsk_burst_recovery.py`](../tests/test_k6_fsk_burst_recovery.py)
- [`tests/test_k6_fsk_parity_recovery.py`](../tests/test_k6_fsk_parity_recovery.py)
- [`tests/test_k6_fsk_rf.py`](../tests/test_k6_fsk_rf.py)
- [`tests/test_k6_fsk_rx_gate.py`](../tests/test_k6_fsk_rx_gate.py)
- [`tests/test_k6_if_capture_live.py`](../tests/test_k6_if_capture_live.py)
- [`tests/test_k6_live.py`](../tests/test_k6_live.py)
- [`tests/test_k6_live_acceptance.py`](../tests/test_k6_live_acceptance.py)
- [`tests/test_k6_live_control.py`](../tests/test_k6_live_control.py)
- [`tests/test_k6_live_control_sim.py`](../tests/test_k6_live_control_sim.py)
- [`tests/test_k6_live_power_sim.py`](../tests/test_k6_live_power_sim.py)
- [`tests/test_k6_live_ptt.py`](../tests/test_k6_live_ptt.py)
- [`tests/test_k6_live_ptt_sim.py`](../tests/test_k6_live_ptt_sim.py)
- [`tests/test_k6_live_remote_ui.py`](../tests/test_k6_live_remote_ui.py)
- [`tests/test_k6_live_remote_ui_sim.py`](../tests/test_k6_live_remote_ui_sim.py)
- [`tests/test_k6_live_v2.py`](../tests/test_k6_live_v2.py)
- [`tests/test_k6_mic_result.py`](../tests/test_k6_mic_result.py)
- [`tests/test_k6_serial_bench.py`](../tests/test_k6_serial_bench.py)
- [`tests/test_k6_soapyfang_sotto_lab.py`](../tests/test_k6_soapyfang_sotto_lab.py)
- [`tests/test_k6_sotto_api28_resource_gate.py`](../tests/test_k6_sotto_api28_resource_gate.py)
- [`tests/test_k6_sotto_api28_resources_host.py`](../tests/test_k6_sotto_api28_resources_host.py)
- [`tests/test_k6_sotto_api29_async_gate.py`](../tests/test_k6_sotto_api29_async_gate.py)
- [`tests/test_k6_sotto_api29_native_host.py`](../tests/test_k6_sotto_api29_native_host.py)
- [`tests/test_k6_sotto_api29_overlay.py`](../tests/test_k6_sotto_api29_overlay.py)
- [`tests/test_k6_sotto_api30_mic_gate.py`](../tests/test_k6_sotto_api30_mic_gate.py)
- [`tests/test_k6_sotto_api30_mic_overlay.py`](../tests/test_k6_sotto_api30_mic_overlay.py)
- [`tests/test_k6_sotto_api30_port.py`](../tests/test_k6_sotto_api30_port.py)
- [`tests/test_k6_sotto_dct_fast.py`](../tests/test_k6_sotto_dct_fast.py)
- [`tests/test_k6_sotto_dv_wire.py`](../tests/test_k6_sotto_dv_wire.py)
- [`tests/test_k6_sotto_e2e_mock.py`](../tests/test_k6_sotto_e2e_mock.py)
- [`tests/test_k6_sotto_fft_zero.py`](../tests/test_k6_sotto_fft_zero.py)
- [`tests/test_k6_sotto_firmware_target.py`](../tests/test_k6_sotto_firmware_target.py)
- [`tests/test_k6_sotto_gp0_irq_capture.py`](../tests/test_k6_sotto_gp0_irq_capture.py)
- [`tests/test_k6_sotto_gp0_pcm.py`](../tests/test_k6_sotto_gp0_pcm.py)
- [`tests/test_k6_sotto_gp0_route_arm_gate.py`](../tests/test_k6_sotto_gp0_route_arm_gate.py)
- [`tests/test_k6_sotto_installed_vfo.py`](../tests/test_k6_sotto_installed_vfo.py)
- [`tests/test_k6_sotto_installed_vfo_overlay.py`](../tests/test_k6_sotto_installed_vfo_overlay.py)
- [`tests/test_k6_sotto_live_arm_gate.py`](../tests/test_k6_sotto_live_arm_gate.py)
- [`tests/test_k6_sotto_live_packet_parity.py`](../tests/test_k6_sotto_live_packet_parity.py)
- [`tests/test_k6_sotto_mic_ab.py`](../tests/test_k6_sotto_mic_ab.py)
- [`tests/test_k6_sotto_mic_cal.py`](../tests/test_k6_sotto_mic_cal.py)
- [`tests/test_k6_sotto_mic_gain_sweep.py`](../tests/test_k6_sotto_mic_gain_sweep.py)
- [`tests/test_k6_sotto_mic_gp0_dma.py`](../tests/test_k6_sotto_mic_gp0_dma.py)
- [`tests/test_k6_sotto_mic_gp0_irq.py`](../tests/test_k6_sotto_mic_gp0_irq.py)
- [`tests/test_k6_sotto_mic_gp0_pcm.py`](../tests/test_k6_sotto_mic_gp0_pcm.py)
- [`tests/test_k6_sotto_mic_raw.py`](../tests/test_k6_sotto_mic_raw.py)
- [`tests/test_k6_sotto_mic_routes.py`](../tests/test_k6_sotto_mic_routes.py)
- [`tests/test_k6_sotto_mic_time.py`](../tests/test_k6_sotto_mic_time.py)
- [`tests/test_k6_sotto_mock_qualify.py`](../tests/test_k6_sotto_mock_qualify.py)
- [`tests/test_k6_sotto_module.py`](../tests/test_k6_sotto_module.py)
- [`tests/test_k6_sotto_opt_bench.py`](../tests/test_k6_sotto_opt_bench.py)
- [`tests/test_k6_sotto_pack.py`](../tests/test_k6_sotto_pack.py)
- [`tests/test_k6_sotto_pcm_rf_build.py`](../tests/test_k6_sotto_pcm_rf_build.py)
- [`tests/test_k6_sotto_rf_stage.py`](../tests/test_k6_sotto_rf_stage.py)
- [`tests/test_k6_sotto_rx4800_consumer.py`](../tests/test_k6_sotto_rx4800_consumer.py)
- [`tests/test_k6_sotto_rx4800_dispatch_gate.py`](../tests/test_k6_sotto_rx4800_dispatch_gate.py)
- [`tests/test_k6_sotto_rx4800_firmware_gate.py`](../tests/test_k6_sotto_rx4800_firmware_gate.py)
- [`tests/test_k6_sotto_rx4800_host.py`](../tests/test_k6_sotto_rx4800_host.py)
- [`tests/test_k6_sotto_rx4800_native_rust.py`](../tests/test_k6_sotto_rx4800_native_rust.py)
- [`tests/test_k6_sotto_rx_stream.py`](../tests/test_k6_sotto_rx_stream.py)
- [`tests/test_k6_sotto_serial_probe.py`](../tests/test_k6_sotto_serial_probe.py)
- [`tests/test_k6_sotto_tail_timing.py`](../tests/test_k6_sotto_tail_timing.py)
- [`tests/test_k6_sotto_tone.py`](../tests/test_k6_sotto_tone.py)
- [`tests/test_k6_sotto_tone_live.py`](../tests/test_k6_sotto_tone_live.py)
- [`tests/test_k6_sotto_tx_stage.py`](../tests/test_k6_sotto_tx_stage.py)
- [`tests/test_k6_sotto_virtual_link.py`](../tests/test_k6_sotto_virtual_link.py)

## Physical measurement records published with checksums

The 1kHz / 2kHz / generator-OFF GP0 bit-density and 8kHz PCM measurements and two sustained DMA IRQ reports are now published as [eight measurement-only JSON files](evidence/k6-sotto-20261125/README.md) with preserved source SHA256. These are operator-attended lab summaries, not raw speech or production voice validation.

## Non-repository evidence

Original physical bench artifacts and full radio SPI/calibration backups remain on the private development workstation. They are **identified by local path and checksum in the technical dossier**, not uploaded blindly to a public repository. If transferring the radio work to another workstation, privately export and authenticate those artifacts, preserving their original SHA256 and associated versioned module image.

## Unresolved integration gates

The current public records explicitly do **not** establish one single standalone K6 image that simultaneously supports reliable physical GP0 mic ADC→8k PCM→Sotto encode→FSK TX and RFIC 4800-FSK RX→Sotto decode→speaker. The UART-probe flash has not succeeded (no bootloader response). Consult the [acceptance plan](K6_SOTTO_ON_RADIO_TECHNICAL_DOSSIER.md#8-proposed-acceptance-plan-in-dependency-order) before treating any component as production-qualified.
