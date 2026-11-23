"""Slim Sotto 1200 interoperability with Flux pager and recorded K6 microphone."""
import json
from pathlib import Path
import numpy as np
import pytest
from modem import pager
from tools.k6_sotto_slim_k6mic_offline import run

CODEC=Path('/storage5/dev/flux-mic-digital-voice/sotto/c/fixed')
SLIM=CODEC/'sotto_slim_enc'
DEC=CODEC/'sotto_dec_fx'
USER_PCM=Path('/home/tyler/flux-bench/micvoice-20261123/sotto-offline-run01/k6mic-resampled-8k.raw')


def _fixture(tmp_path):
    fs=8000
    t=np.arange(fs)/fs
    pcm=np.rint(3500*np.sin(2*np.pi*645*t)).astype('<i2')
    f=tmp_path/'source-pcm.raw';f.write_bytes(pcm.tobytes())
    return f


def test_reject_pcm_not_multiple_of_sotto_frame(tmp_path):
    src=tmp_path/'partial.raw';src.write_bytes(b'\x00'*100)
    with pytest.raises(ValueError,match='full 40ms'):
        run(src,SLIM,DEC,tmp_path/'reject')
    assert not (tmp_path/'reject').exists()


@pytest.mark.skipif(not (SLIM.exists() and DEC.exists()),reason='Sotto slim codec not available here')
def test_generated_slim_frames_are_crc_valid_and_receive_decodes(tmp_path):
    x=_fixture(tmp_path)
    out=tmp_path/'slim'
    r=run(x,SLIM,DEC,out,msg_id=99)
    assert r['source_superframes']==25
    assert r['compressed_bytes']==25*6
    assert r['packet_count']==4 and r['crc_accepted_packets']==4
    assert r['reconstructed_superframes']==25
    assert r['compressed_bits_roundtrip_exact']
    assert r['decoded_pcm_samples']==25*320
    assert r['actual_radio_tx'] is False
    assert r['actual_cortex_m0_timing_measured'] is False
    packets=(out/'slim-l64-packets.bin').read_bytes()
    p=pager.profile('L','1200',depth=1,parity=False,short_last=True)
    assert len(packets)==256
    for j in range(4):
        q=p.unpack(packets[64*j:64*(j+1)])
        assert q is not None and (q['msg_id'],q['seq'],q['total'])==(99,j,25)
    corrupt=bytearray(packets[:64]);corrupt[29]^=2
    assert p.unpack(corrupt) is None
    assert (out/'slim-sotto1200.bit').read_bytes()==(out/'slim-rx-reassembled.bit').read_bytes()
    with pytest.raises(FileExistsError):run(x,SLIM,DEC,out,msg_id=99)


@pytest.mark.skipif(not (SLIM.exists() and DEC.exists() and USER_PCM.exists()),
                    reason='Saved K6 mic/host codec absent outside bench')
def test_saved_real_k6_mic_slim_pipeline(tmp_path):
    a=run(USER_PCM,SLIM,DEC,tmp_path/'radio-mic',msg_id=94)
    assert a['source_superframes']==26
    assert a['compressed_bytes']==156
    assert a['voice_s']==1.04
    assert a['packet_count']==4 and a['reconstructed_superframes']==26
    assert a['decoded_pcm_samples']==8320
    assert a['compressed_bits_roundtrip_exact']
    assert Path(a['local_private_wav']).exists()
    # Slim and full encoder are intentionally distinct quantizers; 6B superframes
    # conform to the same Sotto 1200 wire format, not bitwise identity.
    full=Path('/home/tyler/flux-bench/micvoice-20261123/sotto-offline-run01/k6mic-sotto1200.bit')
    assert (tmp_path/'radio-mic/slim-sotto1200.bit').read_bytes()!=full.read_bytes()
