import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import k6_store


def test_profile_planner_is_read_only(monkeypatch):
    def forbid(*args, **kwargs):
        raise AssertionError('planner must not launch subprocesses or touch radio')
    monkeypatch.setattr(k6_store.subprocess, 'run', forbid)
    client = k6_store.create_app().test_client()
    page = client.get('/profiles')
    assert page.status_code == 200
    assert b'Build your radio' in page.data
    assert b'SOTTO' in page.data
    for profile in (b'CE', b'FCC', b'GB', b'PMR446', b'Unrestricted'):
        assert profile in page.data
    assert page.data.count(b'name="tx_profile"') == 10
    response = client.post('/profiles', data={'pack': ['scan', 'data']})
    assert response.status_code == 200
    assert b'K6_FSK_RX_SERVICE' in response.data
    assert b'K6_SCAN_SERVICE' in response.data
    assert b'explore_scan' in response.data
    assert client.post('/profiles', data={'pack': 'sotto'}).status_code == 400
    assert client.post('/profiles', data={'pack': 'nonexistent'}).status_code == 400


def test_live_preview_theme_and_validation(monkeypatch):
    def forbid(*args, **kwargs):
        raise AssertionError('preview must not start a build')
    monkeypatch.setattr(k6_store.subprocess, 'run', forbid)
    client = k6_store.create_app().test_client()
    response = client.post('/profiles/preview', data={'theme': 'classic', 'pack': ['data', 'scan']})
    assert response.status_code == 200
    result = response.get_json()
    assert result['plan']['theme'] == 'classic'
    assert set(result['plan']['packs']) == {'base', 'data', 'scan'}
    assert 'cardlink' in result['plan']['modules']
    assert client.post('/profiles/preview', data={'theme': '../bad'}).status_code == 400
    unlocked = client.post('/profiles/preview', data={'tx_profile': 'unlocked', 'pack': 'scan'})
    assert unlocked.status_code == 200
    assert unlocked.get_json()['plan']['tx_profile'] == 'unlocked'
    assert client.post('/profiles/preview', data={'tx_profile': 'bad'}).status_code == 400
    assert client.post('/profiles/build', data={'theme': '../bad'}).status_code == 400
    assert client.post('/profiles/preview', data={'pack': 'sotto'}).status_code == 400
    page = client.post('/profiles', data={'theme': 'minimal', 'pack': 'scan'})
    assert b'--theme minimal' in page.data
    assert b'value="minimal" checked' in page.data


def test_base_target_overrun_is_warning_under_hard_limit(monkeypatch,tmp_path):
    import k6_flash_estimate
    data=json.loads(k6_flash_estimate.ESTIMATES.read_text())
    data["base_bytes"]=101184
    path=tmp_path/"estimates.json";path.write_text(json.dumps(data))
    monkeypatch.setattr(k6_flash_estimate,"ESTIMATES",path)
    client = k6_store.create_app().test_client()
    response = client.post('/profiles/preview', data={'pack': [], 'iq': '0'})
    assert response.status_code == 200
    estimate = response.get_json()['estimate']
    assert estimate['used'] == 101184
    assert estimate['limit'] == 122880
    assert estimate['allowed'] is True
    assert estimate['base_target_over'] is True
    assert estimate['base_target_over_by'] == 2880
    assert b'2,880 bytes above the 96 KiB Base design goal' in client.post('/profiles', data={'pack': [], 'iq': '0'}).data


def test_flash_estimate_warns_but_does_not_block(monkeypatch, tmp_path):
    from k6_build_jobs import BuildJobs
    calls = []
    class Process:
        def poll(self): return None
    def runner(command, **kwargs):
        calls.append(command)
        return Process()
    app = k6_store.create_app()
    app.config['build_jobs'] = BuildJobs(tmp_path, runner=runner)
    client = app.test_client()
    packs = ['scan', 'data', 'ota', 'designer']
    preview = client.post('/profiles/preview', data={'pack': packs, 'iq': '0'})
    assert preview.status_code == 200
    estimate = preview.get_json()['estimate']
    assert estimate['used'] > estimate['limit']
    assert estimate['allowed'] is False
    assert estimate['build_allowed'] is True
    response = client.post('/profiles/build', data={'pack': packs, 'iq': '0'})
    assert response.status_code == 302
    assert len(calls) == 1
    assert '--no-iq' in calls[0]


def test_four_bundle_iq_reference_fits():
    client = k6_store.create_app().test_client()
    data = client.post('/profiles/preview', data={'pack': ['scan','designer','data'], 'iq':'1'}).get_json()
    assert data['ok'] and data['estimate']['measured_profile']
    assert data['estimate']['used'] <= 122880
    assert data['estimate']['build_allowed']


def test_custom_theme_is_buildable_and_rejects_invalid_markers():
    client = k6_store.create_app().test_client()
    token = 'custom-01-c0f0fcfefcf0c000'
    response = client.post('/profiles/preview', data={'theme': token, 'pack': 'scan'})
    assert response.status_code == 200
    assert response.get_json()['plan']['theme'] == token
    page = client.post('/profiles', data={'theme': token})
    assert b'Theme editor' in page.data
    assert token.encode() in page.data
    assert b'theme-editor.js' not in page.data
    assert b'data-theme-editor' not in page.data
    assert b'theme-editor.js' in client.get('/themes').data
    for invalid in ('custom-00-0000000000000000', 'custom-11-0100000000000000',
                    'custom-11-ffffffffffffffff', 'custom-22-c0f0fcfefcf0c000', 'custom-11-xyz'):
        assert client.post('/profiles/preview', data={'theme': invalid}).status_code == 400
        assert client.post('/profiles/build', data={'theme': invalid}).status_code == 400


def test_factory_themes_are_editable_presets():
    from k6_feature_packs import THEME_EDITOR_PRESETS
    client = k6_store.create_app().test_client()
    for key, token in THEME_EDITOR_PRESETS.items():
        page = client.get('/themes')
        assert page.status_code == 200
        assert f'value="{token}" data-theme-id="{key}"'.encode() in page.data
        assert b'data-editor-custom' in page.data
        assert client.post('/profiles/preview', data={'theme': token}).status_code == 200
    assert 'Flux — factory default'.encode() in client.get('/themes').data


def test_factory_meter_preview_matches_segment_geometry():
    page = k6_store.create_app().test_client().get('/profiles').data
    assert b'x="62" y="39" width="4" height="1"' in page
    assert b'x="102" y="33" width="4" height="7"' in page
    assert b'x="107.5" y="33.5" width="3" height="6" fill="none"' in page
    assert b'x="122.5" y="33.5" width="3" height="6" fill="none"' in page


def test_theme_splash_preview_uses_firmware_art_source():
    logos = k6_store.theme_splash_preview_logos()
    assert len(logos) == 3 and all(len(rows) == 16 for rows in logos)
    page = k6_store.create_app().test_client().get('/themes').data
    assert ('window.FluxSplashLogos=' + json.dumps(logos)).encode() in page
    source = (Path(k6_store.__file__).with_name('k6_store_templates') /
              'static' / 'theme-widgets.js').read_text()
    assert 'const logos=window.FluxSplashLogos' in source


def test_saved_theme_is_available_in_builder_not_inline_editor(monkeypatch, tmp_path):
    monkeypatch.setenv('FLUX_THEME_LIBRARY', str(tmp_path / 'themes.json'))
    client = k6_store.create_app().test_client()
    token = 'custom-01-c0f0fcfefcf0c000'
    assert client.get('/themes').status_code == 200
    editor = client.get('/themes').data
    assert b'data-theme-editor' in editor
    assert b'data-builder-themes' not in editor
    assert b'name="theme"' not in editor
    saved = client.post('/themes/library', data={'name': 'Test design', 'theme': token})
    assert saved.status_code == 200
    token = saved.json['theme']['token']
    page = client.post('/profiles', data={'pack': 'designer', 'theme': token, 'theme_name': 'Test design'})
    assert page.status_code == 200
    assert b'data-theme-editor' not in page.data
    assert b'data-theme-name="Test design" checked' in page.data
    assert client.post('/profiles/preview', data={'pack':'designer','theme': token}).json['plan']['theme'] == token
    assert client.delete('/themes/library', data={'name': 'Test design'}).status_code == 200
    assert b'Test design' not in client.get('/profiles').data
