#!/usr/bin/env python3
"""Archive the exact Flux and pinned upstream source trees for a release."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]
AURA = ROOT / 'firmware/k6-aura/build/aura-fd6818b-port'


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args])


def pinned_sources():
    revision = git(ROOT, 'rev-parse', 'HEAD').decode().strip()
    sotto = git(ROOT, 'rev-parse', revision + ':sotto').decode().strip()
    script = git(ROOT, 'show', revision + ':firmware/k6-aura/build-fd6818b.sh').decode()
    aura = re.search(r'AURA_FD6818B_REV:-([0-9a-f]{40})', script).group(1)
    return [('flux', ROOT, revision), ('sotto', ROOT/'sotto', sotto), ('aura', AURA, aura)]


def package(output):
    output.parent.mkdir(parents=True, exist_ok=True)
    metadata = {}
    with tarfile.open(output, 'w:gz') as dest:
        for name, repo, revision in pinned_sources():
            if git(repo, 'rev-parse', 'HEAD').decode().strip() != revision:
                raise ValueError(f'{name}: checkout differs from pinned {revision}')
            raw = git(repo, 'archive', '--format=tar', revision)
            with tarfile.open(fileobj=io.BytesIO(raw)) as src:
                for member in src:
                    if name == 'flux' and member.name == 'sotto':
                        continue
                    prefix = {'flux':'flux/', 'sotto':'flux/sotto/', 'aura':'aura-upstream/'}[name]
                    member.name = prefix + member.name
                    dest.addfile(member, src.extractfile(member) if member.isfile() else None)
            metadata[name] = revision
        data = (json.dumps(metadata, indent=2) + '\n').encode()
        member = tarfile.TarInfo('source-revisions.json')
        member.size = len(data)
        dest.addfile(member, io.BytesIO(data))
    output.with_name(output.name+'.sha256').write_text(f'{hashlib.sha256(output.read_bytes()).hexdigest()}  {output.name}\n')
    return metadata


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    print(json.dumps(package(parser.parse_args().output), indent=2))
