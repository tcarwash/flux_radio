"""First-install safety tests without opening a physical serial device."""
from contextlib import nullcontext
from pathlib import Path
import sys
from types import SimpleNamespace
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import k6_starter_install as starter

@pytest.fixture
def harness(tmp_path, monkeypatch):
    events=[]
    state=SimpleNamespace(cable=[None,None], aura=[False,True], answers=['INSTALL','UPDATE','','FORMAT',''],
                          backup_size=starter.radio.CHIP_BYTES, keyed=False, designer=False)
    backup=tmp_path/'backup.bin'
    lock={'packs':['base','scan'],'tx_profile':'fcc'}
    def artifacts(*args):
        return lock, b'firmware', None, None
    monkeypatch.setattr(starter,'provision_artifacts',artifacts)
    monkeypatch.setattr(starter,'pack_port',lambda port:nullcontext())
    monkeypatch.setattr(starter.radio,'default_backup_path',lambda:str(backup))
    monkeypatch.setattr(starter.radio,'open_port',lambda args:SimpleNamespace(close=lambda:events.append('close')))
    cable=SimpleNamespace(capabilities=lambda:{'cable_only':not state.keyed},end=lambda:events.append('end'))
    monkeypatch.setattr(starter.radio,'open_cable',lambda *a,**kw: cable if state.cable.pop(0) else None)
    monkeypatch.setattr(starter.radio,'CpsLink',lambda *a:SimpleNamespace(handshake=lambda *a,**kw:state.aura.pop(0),end=lambda:events.append('end')))
    def save(*args):
        backup.write_bytes(b'\xff'*state.backup_size)
        events.append('backup')
    monkeypatch.setattr(starter.radio,'backup',save)
    monkeypatch.setattr(starter,'provision_pack',lambda *a,**kw:events.append(kw['action']))
    monkeypatch.setattr(starter.radio,'main',lambda args,**kw:events.append(args[2]) or 0)
    def ask(prompt):
        if 'Type FORMAT' in prompt:
            assert backup.stat().st_size==starter.radio.CHIP_BYTES
            events.append('format_prompt')
        return state.answers.pop(0)
    def run():
        starter.install(tmp_path,{},'/dev/fake',ask=ask,log=lambda *a,**kw:None)
    return state,events,backup,lock,run

def test_stock_backup_precedes_format_and_modules(harness):
    state,events,backup,lock,run=harness
    run()
    assert events.index('flash')<events.index('backup')<events.index('format_prompt')<events.index('install')

def test_existing_flux_backup_precedes_flash(harness):
    state,events,backup,lock,run=harness
    state.cable=[True,True];state.answers=['INSTALL','']
    run()
    assert events.index('backup')<events.index('flash')<events.index('install')

def test_incomplete_first_boot_backup_refuses_format(harness):
    state,events,backup,lock,run=harness
    state.backup_size=32
    with pytest.raises(starter.PackError,match='incomplete'):run()
    assert 'format_prompt' not in events and 'install' not in events

def test_cancel_before_format(harness):
    state,events,backup,lock,run=harness
    state.answers=['INSTALL','UPDATE','','NO']
    with pytest.raises(starter.PackError,match='cancelled before format'):run()
    assert backup.exists() and 'install' not in events

def test_keyed_radio_refused_before_flash(harness):
    state,events,backup,lock,run=harness
    state.cable=[True];state.keyed=True
    with pytest.raises(starter.PackError,match='keyed radio'):run()
    assert 'flash' not in events

def test_designer_artwork_uses_serial_cli(harness):
    state,events,backup,lock,run=harness
    lock['theme_package']={'path':'theme.fluxtheme'}
    lock['boot_splash']={'path':'boot-splash.bin'}
    run()
    assert events.index('install')<events.index('install-theme')<events.index('install-boot-logo')

def test_cancel_before_opening_port(harness):
    state,events,backup,lock,run=harness
    state.answers=['NO']
    with pytest.raises(starter.PackError,match='cancelled'):run()
    assert not events

def test_packed_stock_journal_precedes_format(harness,monkeypatch,tmp_path):
    import k6_user_store_restore as restore
    state,events,backup,lock,run=harness
    lock['storage']={'format':2,'system_pages':[]}
    monkeypatch.setattr(starter.radio,'flash',lambda *a,**kw:events.append('flash'))
    monkeypatch.setattr(starter.radio,'CpsLink',lambda *a:SimpleNamespace(handshake=lambda *a,**kw:state.aura.pop(0),end=lambda:events.append('end'),read=lambda a,n:b'\xff'*n))
    monkeypatch.setattr(restore,'BACKUP_DIR',tmp_path/'journals')
    def provision(*a,**kw):
        assert kw['first_install'].is_file();events.append('install')
        assert kw['first_install'].with_suffix('.kul2').is_file()
    monkeypatch.setattr(starter,'provision_pack',provision)
    run()
    assert events.index('flash')<events.index('backup')<events.index('format_prompt')<events.index('install')
    from k6_user_store_migrate import locations
    assert locations(next((tmp_path/'journals').glob('*.kul2')).read_bytes())=={}
