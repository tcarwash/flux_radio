"""FSK-14 independent recovered Sotto playout privacy/integrity tests.

No RF hardware used, no microphone PCM or codec bytes are printed or
committed; private archive tests skip outside the original K6 bench.
"""
from pathlib import Path
import json
import pytest
from tools.k6_fsk_guard33_mic_playout import run,DECODER,DECODER_SHA,sha_file,private_write
from tools.k6_fsk_guard33_mic_bench import frozen_build

PRIV=Path('/home/tyler/flux-bench/micvoice-20261123/fsk14-mic-guard-private-20261123')
RX=PRIV/'fsk14-mic-guard-analysis-run01.json'
REPORT=PRIV/'fsk14-rf-audio-replay-report.json'
WAV=PRIV/'fsk14-rf-decoded-8k.wav'


def test_host_decoder_hash_and_private_file_guard():
    if not DECODER.is_file():pytest.skip('frozen host codec not available')
    assert sha_file(DECODER)==DECODER_SHA


def test_private_write_creates_0600_and_refuses_overwrite(tmp_path):
    tmp_path.chmod(0o700)
    f=tmp_path/'test.bin'
    private_write(f,b'known test, not microphone data')
    assert f.stat().st_mode & 0o777==0o600
    with pytest.raises(FileExistsError):
        private_write(f,b'changed')


@pytest.mark.skipif(not REPORT.exists() or not WAV.exists(),
                    reason='archived private on-air mic voice playout not present')
def test_original_radio_recovered_audio_is_bit_exact():
    x=json.loads(REPORT.read_text())
    assert x['rf_packets_crc_valid']==4
    assert x['recovered_superframes']==26
    assert x['compressed_voice_bytes']==156
    assert x['compressed_source_bit_exact'] is True
    assert x['pcm_samples_8khz']==8320
    assert x['audio_duration_s']==1.04
    assert x['rf_recovered_wav_equals_original_host_reconstruction'] is True
    assert x['decoded_wav_sha256']==sha_file(WAV)
    assert WAV.stat().st_mode & 0o777==0o600
    assert PRIV.stat().st_mode & 0o777==0o700
    assert len(frozen_build()['expected_packet_hashes'])==4


@pytest.mark.skipif(not RX.exists() or not WAV.exists(),
                    reason='archived private on-air mic voice playout not present')
def test_preexisting_voice_evidence_cannot_be_overwritten():
    with pytest.raises(FileExistsError):
        run(RX,PRIV)
