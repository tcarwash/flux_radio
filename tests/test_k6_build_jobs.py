import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import pytest
import k6_store
from k6_build_jobs import BuildJobs


class FakeProcess:
    def __init__(self):
        self.code = None
    def poll(self):
        return self.code


def test_jobs_serialize_and_report(tmp_path):
    calls = []
    def runner(command, **kwargs):
        calls.append(command)
        return FakeProcess()
    jobs = BuildJobs(tmp_path, runner=runner)
    job_id = jobs.start(['scan', 'data'])
    assert jobs.get(job_id)['state'] == 'running'
    assert calls[0][2:5] == ['build', 'base', 'scan']
    with pytest.raises(ValueError, match='already running'):
        jobs.start(['base'])
    jobs._jobs[job_id]['process'].code = 1
    assert jobs.get(job_id)['state'] == 'failed'
    assert jobs.start(['base']) != job_id
    with pytest.raises(KeyError):
        jobs.get('not-a-real-job')


def test_catalog_build_routes_do_not_touch_radio(tmp_path):
    app = k6_store.create_app()
    jobs = BuildJobs(tmp_path, runner=lambda *args, **kwargs: FakeProcess())
    app.config['build_jobs'] = jobs
    client = app.test_client()
    assert client.post('/profiles/build', data={'pack': ['scan', 'data']}).status_code == 302
    job_id = next(iter(jobs._jobs))
    response = client.get(f'/profiles/build/{job_id}')
    assert response.status_code == 200
    assert b'RUNNING' in response.data
    assert client.post('/profiles/build', data={'pack': 'unknown'}).status_code == 400
    assert client.get('/profiles/build/nonexistent').status_code == 404


def test_catalog_pack_radio_jobs_require_token_and_confirmation(tmp_path):
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        return FakeProcess()

    app = k6_store.create_app()
    jobs = BuildJobs(tmp_path, runner=runner)
    app.config['build_jobs'] = jobs
    job_id = jobs.start(['scan'])
    job = jobs._jobs[job_id]
    job['process'].code = 0
    output = job['folder'] / 'pack'
    output.mkdir()
    (output / 'pack.lock.json').write_text(json.dumps({
        'firmware': {'bytes': 42, 'sha256': 'test'},
        'modules': [{'name': 'surf'}],
    }))
    client = app.test_client()
    client.post('/settings', data={'port': '/dev/ttyUSB0'})
    route = f'/profiles/build/{job_id}/radio'
    assert client.post(route, data={'action': 'flash', 'confirm': 'yes'}).status_code == 403
    assert b'Flash firmware' in client.get(f'/profiles/build/{job_id}').data
    with client.session_transaction() as session:
        token = session['pack_radio_token']
    assert client.post(route, data={'action': 'flash', 'token': token}).status_code == 302
    assert len(calls) == 1
    response = client.post(route, data={'action': 'flash', 'token': token,
                                       'confirm': 'yes', 'mode': 'update'})
    assert calls[-1][-4:] == ['--port', '/dev/ttyUSB0', '--yes', '--already-update']
    assert b'Flash firmware' in client.get(response.location).data
    # The fake child must finish before starting another radio operation.
    for _, _, process, _ in jobs._radio_jobs.values():process.code=0
    client.post(route, data={'action': 'preview', 'token': token})
    assert calls[-1][-1] == '--preview'


def test_theme_used_by_build_job(tmp_path):
    calls = []
    def runner(command, **kwargs):
        calls.append(command)
        return FakeProcess()
    jobs = BuildJobs(tmp_path, runner=runner)
    app = k6_store.create_app()
    app.config['build_jobs'] = jobs
    response = app.test_client().post('/profiles/build', data={'theme': 'minimal', 'pack': 'scan'})
    assert response.status_code == 302
    job_id = next(iter(jobs._jobs))
    assert jobs.get(job_id)['theme'] == 'minimal'
    assert jobs.get(job_id)['tx_profile'] == 'ce'
    assert calls[0][-5:] == ['--tx-profile', 'ce', '--theme', 'minimal', '--iq']

def test_build_compiler_log_is_collapsed(tmp_path):
    app = k6_store.create_app()
    jobs = BuildJobs(tmp_path, runner=lambda *args, **kwargs: FakeProcess())
    app.config['build_jobs'] = jobs
    job_id = jobs.start(['base'])
    client = app.test_client()
    page = client.get(f'/profiles/build/{job_id}').data
    assert b'<details class="readme" id="compiler-output">' in page
    assert b'Compiler output' in page
    jobs._jobs[job_id]['process'].code = 1
    page = client.get(f'/profiles/build/{job_id}').data
    assert b'Build failed.' in page
    assert b'<details class="readme" id="compiler-output">' in page


def test_download_completed_pack(tmp_path):
    import io, zipfile
    from k6_feature_packs import DEFAULT, resolve
    from k6_pack_build import assemble
    from k6_pack_policy import POLICY, gate_environment, storage_environment
    from k6_capability_receipt import write
    app = k6_store.create_app()
    jobs = BuildJobs(tmp_path, runner=lambda *args, **kwargs: FakeProcess())
    app.config['build_jobs'] = jobs
    job_id = jobs.start(['base'], iq=False)
    route = f'/profiles/build/{job_id}/download'
    client = app.test_client()
    assert client.get(route).status_code == 409
    assert client.get('/profiles/build/unknown/download').status_code == 404
    folder = jobs._jobs[job_id]['folder']
    image = folder / 'input.bin'
    image.write_bytes(b'firmware-fixture')
    output = folder / 'pack'
    plan = resolve(json.loads(DEFAULT.read_text()), ['base'])
    assemble(plan, output, image, packed=True)
    write(output/'firmware.receipt.json', output/'firmware.bin', plan, {**gate_environment(plan, json.loads(POLICY.read_text())), **storage_environment(plan)})
    (output/'private.key').write_bytes(b'never export')
    jobs._jobs[job_id]['process'].code = 0
    assert b'Download firmware pack' in client.get(f'/profiles/build/{job_id}').data
    result = client.get(route)
    assert result.status_code == 200 and result.mimetype == 'application/zip'
    assert 'attachment;' in result.headers['Content-Disposition']
    with zipfile.ZipFile(io.BytesIO(result.data)) as z:
        assert set(z.namelist()) == {'INSTALL.txt', 'pack/firmware.bin', 'pack/pack.lock.json', 'pack/firmware.receipt.json'}
        assert z.read('pack/firmware.bin') == image.read_bytes()
    (output/'firmware.bin').write_bytes(b'tampered')
    assert client.get(route).status_code == 409
