#!/usr/bin/env python3
"""Build local Git repositories containing slot-independent K6 module bundles."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from modem import k6mod
import k6_install


def make_repo(name, source, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    aliases = {"aprsapp": "aprs", "aprsbeacon": "aprs-beacon", "aprsgpssync": "aprs-gps-sync", "aprskissrx": "aprs-kiss-rx", "fluxtnc": "flux-tnc", "gw_flux": "gw"}
    dest = output / aliases.get(name,name)
    if dest.exists():
        raise FileExistsError(f"{dest} already exists; not overwriting it")
    with tempfile.TemporaryDirectory(prefix=".building-", dir=output) as temp:
        build = Path(temp)
        module_id = "org.ag7su.k6."+("aprs" if name=="aprsapp" else name) if name in aliases and name != "gw_flux" else f"org.blowfang.k6.{'gw' if name == 'gw_flux' else name}"
        args = SimpleNamespace(source=str(source), id=module_id, level=None,
                               output=str(build))
        k6_install.cmd_module_bundle(args, log=lambda *args: None)
        manifest = build / "manifest.json"
        data = json.loads(manifest.read_text())
        data["flux_source_module"] = name
        manifest.write_text(json.dumps(data, indent=2) + "\n")
        if name == "gw_flux":
            readme = "# Groundwave\n\nCompact local two-player artillery game. No RF multiplayer or transmit path. Local play confirmed on FD6818B hardware.\n"
        elif name == "explore_scan":
            readme = (
                "# EXPLORE_SCAN\n\n"
                "Receive only. Scans the supported VHF/UHF band sets one segment at a time, ranks "
                "up to 5 likely repeater candidates, lets you try a candidate frequency, and "
                "ignores/unignores candidates. While trying one, the host's shared CW and CTCSS/DCS "
                "decoders can add a confirmed callsign and tone to the candidate. Press B on a "
                "selected candidate to tune Aura VFO B to it. A provisional row appears as soon as "
                "a repeater-band carrier is confirmed; repeated overs and decoder cues update its "
                "grade. Candidate observations are held in RAM for the current run. Saved-repeater "
                "lookup is omitted in this memory-constrained module build.\n\n"
                "Controls: MENU switches between scan status and the candidate list; UP/DOWN "
                "select a candidate; Side 1 parks and listens for CW/CTCSS/DCS, then any key "
                "resumes; B hands the candidate to Aura VFO B; `*` ignores/restores; `#` changes "
                "band set; EXIT leaves the module. "
                "Key 5 reports that saving is unavailable in this compact build.\n\n"
                "This is an experimental port of the original Blowfang Explore workflow for the "
                "FD6818B Flux host. The host links the shared `c/k6/xscan.c` scanner and "
                "`c/k6/explore.c` candidate engine into firmware flash; the module holds only "
                "the UI and working state in the module arena. Manual Try uses the host's shared "
                "decoder APIs. Automatic CW listening, DMR analysis, and table writes are not "
                "ported yet. The host renders the compact original-style Explore list UI. "
                "Requires module API level 21.\n\n"
            )
        elif name == "fluxtnc":
            readme = ("# Flux KISS TNC\n\nOptional Data-pack port-zero KISS client"
                      " with APRS-1200 TX and mode list/get/set controls."
                      " Launch manually from Catalog; cable UART is exclusively owned"
                      " while active. Other installed modem modules must separately"
                      " implement the packet-provider contract; Sotto voice is not"
                      " automatically packet TX capable. Bench validation pending.\n\n")
        elif name in aliases:
            readme = {
                "aprsapp": "# APRS\n\nNative software Bell 202 receive and packet browser. No optional feature set.\n",
                "aprsbeacon": "# APRS Beacon\n\nManually confirmed position beacon; operation confirmed on FD6818B hardware.\n",
                "aprsgpssync": "# GPS Sync\n\nOne-shot position import into native settings from AG7SU-7. API 23 required.\n",
                "aprskissrx": "# APRS KISS RX\n\nReceive-only KISS serial bridge; physical UART validation pending.\n",
            }[name]
        elif name in ("rflimits", "rffskrx", "rffsktx", "rfbaseb"):
            title, summary = {
                "rflimits": ("RF Limits", "Receive-only frequency survey on both chip IDs; raw status and RSSI at each frequency and +30 kHz. No PLL-lock claim."),
                "rffskrx": ("RF FSK RX", "FD6818B Data feature set, 60-second managed native 1200-baud FIFO listener. Raw words over serial."),
                "rffsktx": ("RF FSK TX", "FD6818B Data feature set. Dummy load required; fresh MENU confirmation for one low-power test frame on selected 2m/70cm VFO. Configured APRS call required."),
                "rfbaseb": ("RF IF B", "FD6818B only, receive-only IF-selector and GP0 capture diagnostic. Needs steady reference carrier; serial report."),
            }[name]
            readme = f"# {title}\n\n{summary} Experimental; RF validation pending. See docs/k6-rf-lab.md.\n\n"
        else:
            readme = (
                f"# {name.upper()}\n\n"
                "Experimental original Flux module; not validated on the compact FD6818B host. "
                "Check this module's documented behavior and radio compatibility before use. "
                "Transmit only when the module explicitly supports it and you have verified its settings.\n\n"
            )
        readme += (
                f"Source: `c/k6/mod/{name}.c` in the Flux checkout. This local bundle is built "
                "from that checkout and uses an automatically allocated library slot. Rebuild it after "
                "updating the source. See the main project's documentation and MIT license.\n")
        (build / "README.md").write_text(readme)
        subprocess.run(["git", "init", "--quiet", str(build)], check=True)
        build.rename(dest)
    return dest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "local-modules")
    args = parser.parse_args(argv)
    # Keep the local starter catalog aligned with the current FD6818B Flux host.
    # Other upstream modules can be added when they are ported and validated there.
    names = ["surf", "vfo", "gw_flux", "cardlink", "explore_scan", "aprsapp", "aprsbeacon", "aprsgpssync", "aprskissrx", "fluxtnc", "rflimits", "rffskrx", "rffsktx", "rfbaseb"]
    failed = []
    for name in names:
        source = ROOT / "c/k6/mod" / (name + ".c")
        try:
            path = make_repo(name, source, args.output)
            print(f"created {path}", flush=True)
        except (OSError, ValueError, k6_install.InstallError, subprocess.CalledProcessError) as exc:
            print(f"FAILED {name}: {exc}", file=sys.stderr, flush=True)
            failed.append(name)
    print(f"created {len(names) - len(failed)}/{len(names)} repositories")
    return bool(failed)


if __name__ == "__main__":
    sys.exit(main())
