# Upstream projects and licensing

Flux preserves its Blowfang ancestry and original copyright notices.

| Project | Role | Source |
| --- | --- | --- |
| Blowfang | Original project, modem and module infrastructure | https://github.com/baitisj/blowfang |
| Aura | Rust firmware base | https://github.com/sophiel-meow/bf-k61-firmware-aura |
| Sotto | Pinned source submodule | https://github.com/baitisj/sotto |

The root MIT notice applies to Blowfang-derived MIT code and MIT-licensed Flux contributions. Aura is GPL-3.0-only; Aura-derived patches and code are subject to that license. See `firmware/k6-aura/LICENSE.md` and `LICENSES/GPL-3.0-only.txt`. The linked Aura firmware is distributed under GPL-3.0; the MIT notice does not override those obligations. Sotto and other dependencies retain their own notices and license terms.

For a firmware release, provide the complete corresponding source for the actual image, including the pinned Aura revision, Flux patches and build scripts, pinned submodule source, and any additional covered source needed to build and install it. Keep the original notices. The source revision is recorded in `firmware/k6-aura/build-fd6818b.sh`; the Sotto revision is recorded in the Git submodule entry. Do not publish device-specific keys, calibration backups or proprietary bootloader dumps.

Release source archives contain the exact Flux Git revision, the pinned Sotto
submodule, and the pristine pinned Aura source in `aura-upstream/`. The Flux
tree includes the patch and build scripts used to produce the firmware; see
`source-revisions.json`. The ordinary starter ZIP is intended for installation,
not as the complete source archive. Build tools and Cargo dependencies can also
be obtained from their upstream sources using the included lockfiles.
