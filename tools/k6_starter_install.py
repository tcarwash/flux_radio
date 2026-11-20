"""Guided, cable-only starter provisioning. No new serial protocol."""
from pathlib import Path
from types import SimpleNamespace
import k6_install as radio
from k6_pack_install import provision_artifacts, provision_pack, PackError, pack_port
from k6_flash_policy import assess

def install(pack, spec, port, *, ask=input, log=print):
    lock, firmware, identity, key = provision_artifacts(pack, spec, None)
    if key is not None or 'ota' in lock['packs']:
        raise PackError('starter-install supports cable-only packs')
    if not assess(len(firmware),lock['packs'])['pass']:
        raise PackError('starter exceeds the application flash limit')
    log('Flux starter: '+ ' + '.join(lock['packs'])+'; TX profile: '+lock['tx_profile'])
    log('Only FD6818B (0x4829). Replaces MCU firmware; SPI backup is not stock firmware recovery.')
    if ask('Confirm compatible hardware and TX profile; type INSTALL to continue: ').strip()!='INSTALL':
        raise PackError('cancelled before opening radio')
    path = Path(radio.default_backup_path())
    if path.exists():
        raise PackError('backup path already exists; wait a minute and retry')
    with pack_port(port):
        connection=radio.open_port(SimpleNamespace(port=port))
        try:
            cable=radio.open_cable(connection,log,wait=3.0)
            if cable is not None:
                try:
                    caps=cable.capabilities()
                    if not caps.get('cable_only'):
                        raise PackError('existing keyed radio: use advanced pack installation with its key')
                    radio.backup(cable,path,log)
                finally:
                    cable.end()
                log('Backup saved: '+str(path))
                # Existing-radio flash is separately authenticated by provision_pack.
                mode='cable'
            else:
                mode='update'
                log('No Flux session. Stock firmware may not expose the backup protocol.')
                link=radio.CpsLink(connection,log)
                if link.handshake(radio.AURA_HANDSHAKE,wait=3.0):
                    try: radio.backup(link,path,log)
                    finally: link.end()
                    log('Backup saved: '+str(path))
                if ask('Turn radio off; hold both side keys while powering on. Confirm UPDATE; type UPDATE: ').strip()!='UPDATE':
                    raise PackError('cancelled before flash')
        finally:
            connection.close()
    if (mode == "cable" or path.exists()) and (not path.is_file() or path.stat().st_size != radio.CHIP_BYTES):
        raise PackError('backup incomplete; refusing to flash')
    packed=lock.get('storage',{}).get('format')==2
    journal=None
    # Stock bootloader cannot read SPI; newly flashed firmware exposes a
    # read-only dump before any destructive first-boot formatting.
    if packed and mode=='update':
        with pack_port(port):
            connection=radio.open_port(SimpleNamespace(port=port))
            try:radio.flash(connection,firmware,None,'update',log=log)
            finally:connection.close()
    else:provision_pack(pack,spec,port,action='flash',mode=mode,log=log)
    # Do not wait for a legacy codebook banner; cable-only images may omit it.
    ask('If UPDATE remains, power cycle normally. Do not press MENU at FORMAT FLASH?. Press Enter: ')
    with pack_port(port):
        connection=radio.open_port(SimpleNamespace(port=port))
        try:
            cable=radio.open_cable(connection,log,wait=3.0)
            if cable is not None:
                cable.end()
                if not path.exists():
                    raise PackError('no pre-format backup available; stop and investigate before installing modules')
                if packed and mode=='update':raise PackError('expected first-boot backup screen; do not initialize an unidentified library')
            else:
                link=radio.CpsLink(connection,log)
                if not link.handshake(radio.AURA_HANDSHAKE,wait=10.0):
                    raise PackError('no first-boot backup session; do not format')
                try:
                    if not path.exists():
                        radio.backup(link,path,log)
                    if packed and mode=='update':
                        from k6_user_store_restore import prepare_first_install,BACKUP_DIR
                        journal=prepare_first_install(link,BACKUP_DIR,identity,port,path,log)
                finally:
                    link.end()
                if not path.exists() or path.stat().st_size!=radio.CHIP_BYTES:
                    raise PackError('backup missing or incomplete; do not format')
                log('Backup saved: '+str(path))
                if ask('Verify FORMAT FLASH? on radio. Type FORMAT to authorize, then press MENU on radio: ').strip()!='FORMAT':
                    raise PackError('cancelled before format')
                ask('Press MENU on radio now, wait for VFO screen, then press Enter: ')
        finally:
            connection.close()
    if journal is not None:
        provision_pack(pack,spec,port,action='install',log=log,first_install=journal)
    else:provision_pack(pack,spec,port,action='install',log=log)
    if lock.get('theme_package'):
        theme=lock['theme_package']
        # Use normal typed theme installer instead of reflashing firmware.
        if radio.main(['--port',port,'install-theme',str(pack/theme['path']),'--yes'],log=log,ask=ask) != 0:
            raise PackError('theme installation failed; firmware and modules may already be installed')
    if lock.get('boot_splash'):
        if radio.main(['--port',port,'install-boot-logo',str(pack/lock['boot_splash']['path']),'--yes'],log=log,ask=ask) != 0:
            raise PackError('startup artwork installation failed; firmware and modules may already be installed')
    log('Starter installed. Backup: '+str(path))
