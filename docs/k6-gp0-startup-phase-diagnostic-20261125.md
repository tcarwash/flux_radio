# GP0 startup-phase hardware diagnostic (2026-11-25 PST)

Isolated research branch `fix/gp0-phase-probe-20261125`. It includes a snapshot
of the experimental GP0 source changes used by the prior boot-fix build, not
an update to production/main. **Do not merge to main as a boot fix.**

The GP0 boot-fix image (119,820 bytes, RAM0 3,812/3,840) was previously flashed
and froze on the splash screen; the radio was restored to healthy firmware
`b6a3df0d360d3110`. The earlier attempt to add `PHASE 1/2/3` stopped because
of overlay ordering. This branch re-applies the opt-in probe **after** each of
the three post-link branding/raster-boot overlay passes, verifies one source
insertion per label, and keeps the option disabled by default.

Probe checkpoints:
- `PHASE 1 / RX NEXT`: immediately before `app.radio_mut().enter_rx(...)`
- `PHASE 2 / RX READY`: immediately after the receive transition returns
- `PHASE 3 / MAIN LOOP`: first iteration of the normal non-pager scheduler

An unchanged Flux splash implies execution never reached the first checkpoint;
`PHASE 1` implicates receive transition; `PHASE 2` implicates subsequent
scheduler initialization; `PHASE 3` reaches the normal main loop. The visible
label is a *checkpoint*, not a saved fault trace; screens may subsequently be
replaced if the firmware continues to run.

Validated image on workstation:
`/storage5/dev/flux-gp0-phase-probe-20261125-release/flux-fd6818b-gp0-phase-diag.bin`
- SHA256 `f0bdc6d5c22e1b57c0962bb6fbeadeabe43cbdfa8044a747f8679c54a38b0268`
- Size **121,524 / 122,880** bytes; RAM0 **3,828 / 3,840** bytes (12 bytes free)
- Final image contains all three phase strings; 29 passed/3 skipped Sotto/GP0
  tests and 2 passing probe overlay idempotence/error tests.
- The stock K6 bootloader accepted the entire diagnostic image and restarted,
  but the postflash live-serial capabilities and status probes received no reply.
  **The physical display checkpoint is not yet reported.** No module library
  changes or RF transmission during this test.

Restore when needed: verified working `flux-gp0-mic-route-only.bin` at
`/home/tyler/flux-bench/sotto-mic-calibration-20261125/gp0-dma-v1/`, SHA256
`751995d2dd346a2cfbf7503f8b4d722076cec9164a61efa495b3ca884dc67ccb`.
Recovery uses both side keys during power-on to enter `UPDATE`, then the
standard `tools/k6_install.py --port /dev/ttyUSB0 flash --already-update` tool.
Full 2 MiB SPI backup at
`/storage5/dev/flux-sotto-gp0-ptt-optimized-release-20261125/preflash/k6-spi-before-gp0.bin`;
SHA256 `ec856e231599ef83734f436560380470aaf06ab3c9effe7aaff49d237cebbeee`.
