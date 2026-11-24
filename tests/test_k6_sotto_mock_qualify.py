"""Never confuse no-radio KISS fakes with real Sotto modem execution."""
import pytest
from tools.k6_sotto_mock_qualify import MOCK_TESTS,PRESETS,summarize,main


def test_safe_mock_test_allowlist():
    assert 'tests/test_k6_kiss_hil_fake_device.py' in MOCK_TESTS
    assert 'tests/test_k6_sotto_virtual_link.py' in MOCK_TESTS
    assert not any('hardware' in p or 'rf_bench' in p or 'kiss_hil.py' in p for p in MOCK_TESTS)
    assert {p[0] for p in PRESETS}=={'nominal','slower_spi','high_load'}
    assert PRESETS[0][-1] is True and PRESETS[1][-1] is True and PRESETS[2][-1] is False


def test_missing_actual_sotto_source_fail_closed(tmp_path):
    with pytest.raises(SystemExit) as exc:
        main(['--sotto-tree',str(tmp_path),'--quick','--skip-mock-tests'])
    assert exc.value.code==2


def test_report_preserves_success_gates():
    class Fake(dict):
        pass
    r=dict(accepted=True,module_return=26,module_bytes=10192,samples=8320,
           samples_expected=8320,pcm_lost=False,pcm_highwater=2,lease_expired=False,
           codec_frames=26,codec_exact=True,received=6,wire_decoder_agrees=True,
           stack_bytes=600,spi_reads=6244,spi_read_bytes=194368,keyed_reads=5970,
           keyed_read_bytes=159434,first_rf_ms=111.1,
           session=dict(complete=True,offset_2=0),rx=dict(gaps=0,underflows=0,overruns=0),
           tx=dict(active=False,aborted=0,rejected=0,overflows=0))
    v=summarize(r)
    assert v['session_complete'] and v['rx_clean'] and v['tx_clean'] and v['tx_idle']
    assert v['codec_exact'] and v['accepted']


def test_report_distinguishes_safe_abort_from_success():
    r=dict(accepted=False,module_return=-12,module_bytes=10192,samples=6080,
           samples_expected=9600,pcm_lost=True,pcm_highwater=4,lease_expired=False,
           codec_frames=0,codec_exact=False,received=2,wire_decoder_agrees=True,
           stack_bytes=568,spi_reads=3000,spi_read_bytes=90000,keyed_reads=2500,
           keyed_read_bytes=74000,first_rf_ms=121.0,
           session=dict(complete=False,offset_2=0),rx=dict(gaps=0,underflows=0,overruns=0),
           tx=dict(active=False,aborted=1,rejected=0,overflows=0))
    result=summarize(r)
    assert result['tx_idle'] and not result['tx_clean'] and not result['accepted']
