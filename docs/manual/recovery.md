# Recovery and interrupted installs

Flux is experimental firmware for confirmed FD6818B (0x4829) UV-K6/K61 radios.
Keep a copy of your matching starter archive, its checksum, the original SPI
backup, and the original firmware recovery image if you have obtained one.
Flux does not supply or read back the original MCU firmware or bootloader.

## Before any recovery attempt

- Charge the battery and use a known-good programming cable.
- Verify the exact radio board and RF chip, and close other serial programs.
- Save the error output and keep existing backups unchanged. Never upload a
  backup, signing key, or device-specific calibration to a public issue.
- Do not press MENU if FORMAT FLASH? is displayed unless you have the verified
  2 MiB SPI backup from this particular radio.

## Firmware install interrupted

If the radio still enters UPDATE (power off, hold both side keys, power on),
the bootloader may still be usable. Extract the same known-good, compatible
starter ZIP and use its Python environment. Check the artifacts:

```sh
python tools/k6.py inspect starter
```

If UPDATE is visible, reflash the matching starter firmware:

```sh
python tools/k6.py flash-pack starter --port /dev/ttyUSB0 --already-update --yes
```

Substitute the actual serial device (COM5, for example, on Windows). Do not
flash a different chip variant. If the display remains in UPDATE after the
write completes, power cycle normally. Follow the first-boot backup procedure
in [installation](install.md) if FORMAT FLASH? appears.

If the VFO screen returns, preview the module installation and then install
the matching modules:

```sh
python tools/k6.py install-pack starter --port /dev/ttyUSB0 --preview
python tools/k6.py install-pack starter --port /dev/ttyUSB0 --yes
```

This recovers a compatible Flux installation, not factory firmware. A keyed
OTA installation requires its original key and the appropriate advanced pack.

## If UPDATE is unavailable

Stop trying unrelated images. Check battery, power, cable and the side-key
sequence. Seek hardware-specific help; there is no guaranteed recovery
procedure if the bootloader is unavailable.

## SPI backup

The SPI backup holds calibration and stored data. It is not a firmware image.
There is currently no supported general-purpose full SPI restore command.
Do not use raw flash-write tools or another radio's backup to recover settings.
Keep the original backup and seek assistance before attempting restoration.

Report reproducible problems through [GitHub Issues](https://github.com/tcarwash/flux_radio/issues).
Include the radio board/chip, firmware profile, operating system, exact command,
and sanitized output.
