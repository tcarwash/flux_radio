#!/usr/bin/env python3
"""Package a verified cable-only starter with the matching Python installer."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import zipfile
from k6_feature_packs import DEFAULT
from k6_pack_install import provision_artifacts

ROOT = Path(__file__).resolve().parents[1]
STARTERS = {
    'base': ['base'],
    'scan': ['base', 'scan'],
    'designer-scan': ['base', 'scan', 'designer'],
}

def package(profile, pack, output):
    spec = json.loads(DEFAULT.read_text())
    lock, _, _, key = provision_artifacts(pack, spec, None)
    if set(lock['packs']) != set(STARTERS[profile]) or key is not None:
        raise ValueError('not the requested cable-only starter')
    revision = subprocess.check_output(['git','rev-parse','HEAD'], cwd=ROOT, text=True).strip()
    # Include exact tracked Python/config/docs/source, not local keys, outputs or backups.
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory)/'source.zip'
        subprocess.run(['git','archive','--format=zip','-o',str(source),revision],cwd=ROOT,check=True)
        output.parent.mkdir(parents=True, exist_ok=True)
        prefix = output.stem + '/'
        with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as z:
            with zipfile.ZipFile(source) as src:
                for item in src.infolist():
                    if not item.is_dir():
                        z.writestr(prefix+item.filename,src.read(item))
            names = {'pack.lock.json','firmware.receipt.json',lock['firmware']['path']}
            for mod in lock['modules']:
                names.add(mod['path'])
                names.add(str(Path(mod['path']).parent/'manifest.json'))
            for field in ('theme_package','boot_splash'):
                if field in lock: names.add(lock[field]['path'])
            for page in lock.get('storage',{}).get('system_pages',[]):names.add(page['path'])
            for name in sorted(names):
                z.write(pack/name,prefix+'starter/'+name)
            descriptions = {
                'vfo': ('VFO Radio', 'Native VFO radio tools.'),
                'surf': ('SURF', 'Receive-only band surfing and signal discovery.'),
                'explore_scan': ('Explore Scan', 'Scan and rank potential repeater candidates. Requires Scan.'),
            }
            for mod in lock['modules']:
                name = mod['name']
                title, summary = descriptions[name]
                module_path = Path(mod['path'])
                catalog_path = prefix + 'local-modules/' + name + '/'
                manifest = pack / module_path.parent / 'manifest.json'
                z.write(pack / module_path, catalog_path + module_path.name)
                z.write(manifest, catalog_path + 'manifest.json')
                z.writestr(catalog_path + 'README.md', f'# {title}\n\n{summary}\n\nIncluded with the matching Flux starter image.\n')
            z.writestr(prefix+'START-HERE.txt',
                'Flux experimental FD6818B starter\n'
                'Read docs/manual/install.md before replacing stock firmware.\n'
                'Linux or experimental Windows support / Python 3.10+. No compiler needed.\n'
                'Windows: double-click install.bat, or follow the PowerShell guide.\n'
                'Linux:\n'
                'python3 -m venv .venv\n. .venv/bin/activate\n'
                'python -m pip install -r requirements.txt\n'
                'python tools/k6.py starter-install starter --port /dev/ttyUSB0\n'
                'Confirm your radio chip and transmit profile before installing.\n')
            z.writestr(prefix+'starter-release.json',json.dumps({
                'profile':profile,'target':lock['target'],'tx_profile':lock['tx_profile'],
                'packs':lock['packs'],'revision':revision,'experimental':True,
                'recommended':profile=='scan'},indent=2)+'\n')
    sha=hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(output.suffix+'.sha256').write_text(f'{sha}  {output.name}\n')

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('profile',choices=STARTERS)
    parser.add_argument('pack',type=Path)
    parser.add_argument('output',type=Path)
    args=parser.parse_args()
    package(args.profile,args.pack,args.output)
