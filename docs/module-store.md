# Flux Module Catalog

The Catalog is a small local application, not a service running on the radio. It reads Git repositories,
shows the project README and its screenshots, and can send a compatible module bundle to the K6
through the existing serial cable loader.

![Flux Catalog](img/flux-catalog.png)

*Catalog projects are grouped by compatibility and validation status. Screenshots show a development instance; labels, module availability and device settings may differ in newer versions.*

## Start it

```sh
python3 -m pip install -r tools/k6_store_requirements.txt
python3 tools/k6_store.py add https://github.com/OWNER/K6-MODULE --ref main
python3 tools/k6.py catalog
```

Open <http://127.0.0.1:5055>. The web server binds to localhost by default. The add form also accepts
an existing local Git checkout or a directory containing a valid format-1 module bundle. A ref can
be a branch or tag; refresh checks out its latest commit.
Sources are cached below `~/.cache/flux/module-store/repos/`, and the Catalog list is stored in
`~/.config/flux/module-store.json`. Existing Blowfang config and cache locations are detected for backward compatibility.

Use **Device settings** in the top navigation before using radio tools. Choose a detected
serial device or enter its path and confirm it. Flux can remember the last serial device on
the local server, but each new browser session must confirm it: USB device names can change
when cables are reconnected. Browsing, editing themes and building firmware do not require
a radio. The optional key-file path is session-only, and the key contents remain in the local file.

Select **My radio** in the top navigation to inspect library slots over the programming cable.
Enter the serial port and choose **Read radio library**. This runs the existing `k6_install.py
status` command; that command ends the cable session by restarting the radio. Occupied module
slots can link to a matching project in the catalog by module name. Install or update a module from
that project's page. Remove a module by typing `ERASE <slot>` in its confirmation field. The
firmware validates the slot type, erases and verifies the full 16 KB slot, and rejects firmware
pages and help decks. Removal is permanent, and the cable session restarts the radio when it ends.

## Build firmware and design the display

Open **Feature packs**, select your desired services and transmit profile, then preview
the flash budget. The estimate is not a substitute for the final linked-image size.
Choose **Build this profile** to compile.

![Feature pack builder](img/flux-builder.png)

Open **Theme editor** to edit a layout, draw graphics and preview the radio display.
Save a named design and select it in the firmware builder. Designer is required for
custom display themes on the radio.

![Standalone theme editor](img/flux-designer.png)

After a successful build, follow the [installation guide](manual/install.md) before
writing firmware or formatting SPI flash.

## Local starter module repositories

Downloaded starter archives contain the matching starter module packages under
`local-modules/`, so the Catalog can discover them without a compiler. A source
checkout does not contain compiled modules; use the following generator to
populate it with additional local projects. The screenshots show a more extensive
development catalog than the initial starter archive.

Build local Git repositories for the current host's SURF, VFO, and CARD LINK starter modules:

```sh
python3 tools/k6_make_local_catalog.py
```

The generated Git working directories live under `local-modules/` and are discovered by the
catalog by default. Standalone module bundle directories under `local-modules/` are also discovered
directly; they do not need a `.git` directory. These are local build artifacts, not committed
releases. The generator refuses to overwrite an existing directory; move it aside before rebuilding.
These are starter packages for this host, not a certification that every module has been
hardware-tested.

Other module repositories can be added as they are developed for the host. The host verifies
module format and signatures, but it does not sandbox native code. A successful build does not
mean a module will fit or run correctly on hardware.

## Slot-independent bundles

Package one source (or a prebuilt binary with an explicit `--level`) with a stable, globally unique
ID. Keep the same ID across versions:

```sh
python3 tools/k6_install.py module-bundle c/k6/mod/morse.c --id org.blowfang.k6.morse -o /path/to/bundle
python3 tools/k6_install.py --port /dev/ttyUSB0 load /path/to/bundle
```

The manifest omits `slot` for automatic placement and supplies `id`. The installer searches slots
0–8 and 16–31, preserving 9–15 for firmware pages and help decks. It checks the flash before
choosing an empty location, signs for that location and verifies the write. A 24-byte identity
marker inside the signed image identifies subsequent versions, so a reinstall updates that slot;
legacy installations without a marker are not automatically adopted or removed. Duplicate IDs,
insufficient space and a full library are refused before library writes. `load --only` filters
by fixed manifest slots and therefore does not select automatic entries; omit it for these bundles.
Fixed-slot format-1 entries continue to work as before and can overwrite their specified slots.

Live installation is the default for ordinary modules on packed KUL2 firmware. It uses copy-on-write publication and full readback, with no reboot. An explicit `load --legacy` (or the Catalog legacy checkbox) is required for older firmware, system bundles, and envelopes exceeding the negotiated capacity. Fixed-slot legacy updates are in-place and can be interrupted by power loss. The signature authorizes
native code; it does not sandbox it. Keep a backup and install only trusted modules.

## CLI

```sh
python3 tools/k6_store.py add https://github.com/OWNER/K6-MODULE --ref v1.0
python3 tools/k6_store.py list
python3 tools/k6_store.py show module-id
python3 tools/k6_store.py refresh [module-id]
python3 tools/k6_store.py remove module-id
python3 tools/k6_store.py install module-id --port /dev/ttyUSB0 \
  --key-file ~/.config/flux/k6-mod.key --verify
python3 tools/k6_install.py --port /dev/ttyUSB0 remove-slot 30
```

Add prints the catalog ID used by the other commands. `install` calls `tools/k6_install.py load`
with the selected repository's bundle. The browser offers the same explicit action and shows the
installer's output. This module-install action only loads library entries in the
module manifest; the separate firmware builder can flash firmware. Live removal is the default. For older firmware, explicitly select legacy inventory mode before using removal actions, or pass `--legacy` to `remove-slot` or `remove-slots`. No automatic legacy fallback is performed.

## README content

The title is the first Markdown H1. The short card description is the first ordinary paragraph
after it. Add screenshots with normal Markdown image syntax; relative paths are served from the
cached checkout, and HTTPS image URLs are displayed as-is:

```markdown
# Weather Beacon

Receive and display local APRS weather telemetry.

![Radio screen](docs/radio-screen.png)
```

An installable repository contains a format-1 `manifest.json` at the repository root, in `dist/`,
or one directory below the root, plus every file named in its `sha256` map. It uses the existing
bundle fields: `build_id`, `library` entries (`slot`, `kind`, `file`, `name`, `level`), and `sha256`.
The installer verifies the listed hashes and signs each image locally with the user's configured
module key before sending it. Existing bundles such as the VFO/SURF output can be used directly.

## Current scope

- Catalog sources are default or manually added Git URLs or local directories; no central server or account is
  needed.
- Catalog display metadata comes from README text and images. Module slot/API details come from the
  bundle manifest.
- GitHub releases are not queried yet. This prototype clones the repo's selected branch or tag and
  installs a checked-in or committed bundle.
- There is no sandbox for native modules. Only add sources you trust; the radio's module signature
  check authorizes images for the local key but does not constrain what module code can do.


## Default GitHub sources

Maintainers edit `config/flux-catalog.json` to ship default sources with Flux:

```json
{
  "format": 1,
  "sources": [
    {"source": "https://github.com/OWNER/REPO", "ref": "v1.0"}
  ]
}
```

The example URL is a placeholder; the shipped list starts empty until real module
repositories are published. Omit ref to follow the default branch. Defaults merge
with manual sources and local bundles without persisting copies into user config.
CLI removal suppresses a default locally; re-adding its URL and ref restores it.
Prefer reviewed release tags. Opening the catalog fetches repositories but never
installs modules or runs their build scripts automatically.

## Flux Module SDK

Copy [sdk/module-example](../sdk/module-example/) into a standalone repository.
Its README and AGENTS.md provide human and coding-agent guidance, and its build
script uses the canonical Flux compiler and format-1 bundle generator. The example
includes a host-side test and a catalog-ready bundle when built.

## Local module compatibility (FD6818B)

The curated local catalog (`config/flux-catalog.json`) distinguishes **Hardware tested** (operation confirmed on FD6818B by the maintainer, not an exhaustive validation claim), **Flux targeted** (included in the current host/feature-pack design), **Experimental** (useful development builds with outstanding device validation), **WIP / port required** (kept for reference, but not installable from the catalog), and **Blocked** (invalid, oversized or stale bundle). Targeted is not a claim of hardware acceptance. Third-party projects are unreviewed. The separate **Flux native** badge identifies modules explicitly developed or adapted for the native Flux host. It can coexist with any validation status; it does not override compatibility checks. APRS, GPS Sync, Card Link and APRS Beacon carry both the native and hardware-tested badges.

Experimental installs require explicit acknowledgment in the web store or `k6_store.py install ID --port PORT --experimental`. For curated eligible modules the store also checks the radio's reported API ceiling and required feature capabilities before initiating module writes; unsupported capability queries fail closed. This does not prove RF operation, stack safety or hardware compatibility. The low-level installer remains available for deliberate bench development.

The compact host has an 11,776-byte arena, a 1,040-byte LCD reservation and 48 bytes of automatic-slot overhead. A binary that fits still needs enough scratch and execution stack. Legacy direct FD6818 radio paths and hard-coded bench transmitters are not cleared for this host; do not install them as ordinary end-user modules. Groundwave's supported source for the compact host is `gw_flux.c` (local play only, same module ID as the old game). The legacy RF multiplayer implementation needs a managed Data transport port.

To rebuild the curated starter bundles without overwriting anything, run `python3 tools/k6_make_local_catalog.py --output /path/to/new-directory`. Review the resulting manifests/IDs before replacing old local bundles. In particular, old API-6 EXPLORE is not the current API-21 compact build. Local policy records the currently reviewed API levels; update these intentionally when reviewing an ABI change.
