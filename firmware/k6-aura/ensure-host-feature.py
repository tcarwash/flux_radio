#!/usr/bin/env python3
"""Add the Flux host feature to older generated Aura manifests."""
from pathlib import Path
import re
import sys


def ensure_host_feature(path):
    path = Path(path)
    text = path.read_text()
    if re.search(r'^blowfang-host\s*=', text, re.MULTILINE):
        return
    anchor = re.compile(r'^spilib = \[\][ \t]*$', re.MULTILINE)
    if len(anchor.findall(text)) != 1:
        raise ValueError('expected exactly one spilib feature in Aura Cargo.toml')
    path.write_text(anchor.sub('spilib = []\nblowfang-host = ["spilib"]', text, count=1))


if __name__ == '__main__':
    try:
        ensure_host_feature(sys.argv[1])
    except (OSError, ValueError) as exc:
        sys.exit(str(exc))
