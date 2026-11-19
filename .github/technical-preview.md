Experimental technical preview for confirmed Baofeng UV-K6/K61 **FD6818B (0x4829)** hardware only. The FD6818 (0x4819) is not supported.

- **Base + Scan** is recommended: download the appropriate `flux-fd6818b-scan-*.zip`.
- **Designer + Scan** adds custom display themes.
- Choose CE or FCC as appropriate to your local transmit rules.
- Linux is the initial supported platform; Windows starter installation is experimental. Native Windows firmware compilation is not yet supported.

Extract the entire archive and read START-HERE.txt and the installation guide before connecting your radio. No compiler is needed for the prebuilt installer. Compare the SHA-256 checksum before installation.

Flashing replaces MCU firmware. Keep a verified, radio-specific 2 MiB SPI backup. Do not confirm FORMAT FLASH? until the backup has succeeded. This backup cannot restore the original MCU firmware.

[Installation guide](https://github.com/tcarwash/flux_radio/blob/main/docs/manual/install.md) · [Recovery](https://github.com/tcarwash/flux_radio/blob/main/docs/manual/recovery.md) · [Report problems](https://github.com/tcarwash/flux_radio/issues)

The corresponding-source tarball includes the pinned Flux, Aura and Sotto code. The installer tests exercise simulated serial transport; Windows hardware validation and additional radio variants need community testing.
