"""Background catalog jobs backed by the unified K6 CLI."""
import json
from pathlib import Path
import re
import subprocess
import sys
import threading
import uuid

ROOT = Path(__file__).resolve().parents[1]
JOBS_DIR = ROOT / 'firmware/k6-aura/out-fd6818b/web-builds'


class BuildJobs:
    def __init__(self, directory=JOBS_DIR, runner=subprocess.Popen):
        self.directory = Path(directory)
        self.runner = runner
        self._lock = threading.Lock()
        self._active = None
        self._jobs = {}
        self._radio_jobs = {}

    def start(self, packs, theme='flux', tx_profile='ce', theme_name='', iq=False):
        from k6_feature_packs import DEFAULT, resolve
        from k6_pack_policy import POLICY, validate
        spec = json.loads(DEFAULT.read_text())
        validate(spec, json.loads(POLICY.read_text()))
        plan = resolve(spec, packs or ['base'], theme, tx_profile)
        with self._lock:
            if self._active and self._active.poll() is None:
                raise ValueError('another firmware build is already running')
            self._active = None
            job_id = uuid.uuid4().hex
            folder = self.directory / job_id
            folder.mkdir(parents=True, exist_ok=False)
            log = folder / 'build.log'
            output = folder / 'pack'
            command = [sys.executable, str(ROOT / 'tools/k6.py'), 'build', *plan['packs'], '--output', str(output), '--tx-profile', plan['tx_profile'], '--theme', plan['theme']]
            if iq:command.append('--iq')
            if theme_name:
                command += ['--theme-name', theme_name]
            with log.open('wb') as stream:
                process = self.runner(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT)
            self._active = process
            self._jobs[job_id] = {'process': process, 'packs': plan['packs'], 'theme': plan['theme'], 'theme_name': theme_name, 'tx_profile': plan['tx_profile'], 'folder': folder,
                                  'command': command}
            return job_id

    def start_radio(self, build_id, action, port, key_file, already_update=False, theme_slot=None, recovery=None):
        with self._lock:
            if any(process.poll() is None for _, _, process, _ in self._radio_jobs.values()):
                raise ValueError('another radio operation is already running')
            build = self.get(build_id)
            if build['state'] != 'success':
                raise ValueError('build must finish successfully before provisioning')
            job_id = uuid.uuid4().hex
            if action == 'splash':
                splash = build['lock'].get('boot_splash')
                if not splash:
                    raise ValueError('this build has no full-screen boot artwork')
                splash_path = Path(build['output']) / splash['path']
                if not splash_path.is_file():
                    raise ValueError('the boot artwork file is missing from this build')
                command = [sys.executable, '-u', str(ROOT / 'tools/k6_install.py'), '--port', port,
                           'install-boot-logo', str(splash_path), '--yes']
            elif action == 'theme':
                theme_path = Path(build['output']) / 'theme.fluxtheme'
                if not build['lock'].get('theme_package') or not theme_path.is_file():
                    raise ValueError('this build has no installable theme package')
                command = [sys.executable, '-u', str(ROOT / 'tools/k6_install.py'), '--port', port]
                if key_file:
                    command += ['--key-file', key_file]
                command += ['install-theme', str(theme_path), '--slot', 'auto', '--yes']
            else:
                verbs = {'flash': 'flash-pack', 'install': 'install-pack',
                         'preview': 'install-pack', 'flash-install': 'flash-install-pack'}
                if action not in verbs:
                    raise ValueError('unknown radio action')
                command = [sys.executable, '-u', str(ROOT / 'tools/k6.py'),
                           verbs[action],
                           build['output'], '--port', port,
                           '--preview' if action == 'preview' else '--yes']
                if key_file:
                    command += ['--key-file', key_file]
                if already_update:
                    command.append('--already-update')
                if recovery is not None:
                    if action != 'flash-install':raise ValueError('recovery requires full installation')
                    command += ['--recover',str(recovery)]
            log = self.directory / build_id / f'radio-{job_id}.log'
            with log.open('wb') as stream:
                process = self.runner(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT,
                                      stdin=subprocess.DEVNULL)
            self._radio_jobs[job_id] = (build_id, action, process, log)
            return job_id

    def get_radio(self, build_id, job_id):
        record = self._radio_jobs.get(job_id)
        if record is None:
            if not re.fullmatch(r'[0-9a-f]{32}', build_id) or not re.fullmatch(r'[0-9a-f]{32}', job_id):
                raise KeyError(job_id)
            log = self.directory / build_id / f'radio-{job_id}.log'
            if not log.is_file():
                raise KeyError(job_id)
            output = log.read_text(errors='replace')[-16000:]
            action = ('splash' if 'Full-screen boot bitmap' in output else
                      'theme' if 'Theme verified in slot' in output else
                      'flash-install' if 'waiting for the flashed firmware to start' in output else
                      'flash' if 'bootloader' in output or 'flashing' in output else
                      'preview' if 'preview' in output.lower() else 'install')
            state = ('success' if 'bundled modules installed and read back successfully' in output or
                     'Theme verified in slot' in output or
                     'Full-screen boot bitmap verified' in output or
                     'Full-screen boot bitmap is already current' in output or
                     (action == 'flash' and 'flashed; the radio restarts' in output) else
                     'failed' if 'error:' in output.lower() or 'no answer from the bootloader' in output else 'unknown')
            return {'action': action, 'state': state, 'log': output}
        owner, action, process, log = record
        if owner != build_id:
            raise KeyError(job_id)
        code = process.poll()
        return {'action': action, 'state': 'running' if code is None else ('success' if code == 0 else 'failed'),
                'log': log.read_text(errors='replace')[-16000:]}

    def get(self, job_id):
        job = self._jobs.get(job_id)
        if job is None:
            if not re.fullmatch(r'[0-9a-f]{32}', job_id):
                raise KeyError(job_id)
            folder = self.directory / job_id
            output = folder / 'pack' / 'pack.lock.json'
            if not output.is_file():
                raise KeyError(job_id)
            lock = json.loads(output.read_text())
            log = (folder / 'build.log').read_text(errors='replace') if (folder / 'build.log').is_file() else ''
            return {'id': job_id, 'packs': lock['packs'], 'theme': lock['theme'],
                    'tx_profile': lock.get('tx_profile', 'ce'), 'state': 'success',
                    'exit_code': 0, 'log': log[-16000:], 'lock': lock, 'output': str(output.parent)}
        code = job['process'].poll()
        output = job['folder'] / 'pack' / 'pack.lock.json'
        lock = json.loads(output.read_text()) if code == 0 and output.is_file() else None
        log = (job['folder'] / 'build.log').read_text(errors='replace')
        return {'id': job_id, 'packs': job['packs'], 'theme': job['theme'], 'tx_profile': job.get('tx_profile', 'ce'), 'state': 'running' if code is None else ('success' if code == 0 and lock else 'failed'),
                'exit_code': code, 'log': log[-16000:], 'lock': lock, 'output': str(output.parent) if lock else None}
