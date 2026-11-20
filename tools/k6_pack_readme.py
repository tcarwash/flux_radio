"""Installation instructions bundled with a downloaded Catalog firmware pack."""


def readme(lock):
    ota = 'ota' in lock['packs']
    key = ' --key-file /path/to/module.key' if ota else ''
    win_key = ' --key-file C:\\keys\\module.key' if ota else ''
    return f'''# Flux firmware pack — installation guide

- Target: {lock['target']} (Baofeng UV-K6/K61, FD6818B chip ID 0x4829)
- Feature sets: {', '.join(lock['packs'])}
- TX profile: {lock['tx_profile']}
- Theme: {lock['theme']}
- Firmware size: {lock['firmware']['bytes']:,} bytes
- Serial IQ: {'enabled' if 'serial-iq' in lock.get('optional_services', []) else 'disabled'}
- System pages: {len(lock.get('storage', {}).get('system_pages', []))}

## Before you flash

This is experimental firmware. Confirm the radio chip and board revision;
FD6818 (0x4819) is not compatible. Use a charged battery and a working USB
programming cable. Keep power and cable connected until verification finishes.
Close other applications using the port. Flashing replaces the MCU firmware;
the installer cannot read back or restore the original MCU image. Keep any
existing SPI/calibration backup and an independently obtained recovery image.

Extract the entire ZIP. Do not flash firmware.bin alone: the matching SPI
system pages, module bundles and verification files must stay together.
This archive contains the pack, not the installer. Use a matching version of
Flux from https://github.com/tcarwash/flux_radio and its Python dependencies.
No firmware compiler is required to install a previously built pack.

## Linux and macOS

Open a terminal in your Flux checkout (not the extracted pack directory).
Install Python 3.10+ if necessary, then run:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python -m serial.tools.list_ports -v
```

Replace `/path/to/pack` below with the absolute path to the extracted `pack/`
folder, and `/dev/ttyUSB0` with your cable's actual port (on macOS usually
`/dev/cu.*`). First inspect without writing to the radio:

```sh
python tools/k6.py inspect "/path/to/pack"
```

For a radio already running Flux, power it on normally, then run:

```sh
python tools/k6.py flash-install-pack "/path/to/pack" --port /dev/ttyUSB0{key} --yes
```

For extra safety, make a full SPI backup before upgrading with
`python tools/k6.py radio --port /dev/ttyUSB0 backup -o before-flux.bin`.
Keep the backup outside the pack directory.

## Windows (experimental)

Install Python 3.10+ and open PowerShell in your Flux checkout:

```powershell
py -3 -m venv .venv
.\\.venv\\Scripts\\python.exe -m pip install -r requirements.txt
.\\.venv\\Scripts\\python.exe -m serial.tools.list_ports -v
.\\.venv\\Scripts\\python.exe tools/k6.py inspect "C:\\Downloads\\flux\\pack"
.\\.venv\\Scripts\\python.exe tools/k6.py flash-install-pack "C:\\Downloads\\flux\\pack" --port COM5{win_key} --yes
```

Replace COM5 and the folder path with your actual values. If the port is absent,
check Device Manager for the programming cable and install its manufacturer's
appropriate driver. Windows physical installation remains experimental.

## First installation / recovery

Do not use the running-Flux upgrade command on stock firmware. Follow the
matching checkout's `docs/manual/install.md` and use the guided starter-install
procedure for a supported cable-only starter pack. Custom downloaded profiles
may require first installing a supported starter, then upgrading to this pack.
Never confirm FORMAT FLASH? before a verified SPI backup exists. If a packed
upgrade fails, retain its recovery journal and backup; follow the recovery
instructions rather than formatting or repeatedly flashing blindly.
{'This OTA pack requires your own existing module key. No key is included; use the key-file option above. For first installation, use a compatible cable-only starter first.' if ota else 'This cable-only pack does not require a module signing key.'}

## After installation

Wait for the radio to restart and the installer to finish verification. Check
that the radio opens normally, your memories and settings are present, and the
expected modules appear. Inspect the installation log if any component fails.

The pack's hashes and receipt verify artifact consistency, not publisher identity.
Install only a trusted pack. Keep the complete ZIP for later use.
'''
