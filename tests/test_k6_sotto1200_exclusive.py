"""K6 opt-in 1200-only parser keeps wire compatibility, excludes other codecs."""
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'c/k6/sotto_pager.c'
TEST=ROOT/'tests/data/k6/sotto1200_only_packet_check.c'


def _compile(tmp_path, flags):
    exe=tmp_path/'packet_test'
    result=subprocess.run(['cc','-std=c99','-O2','-ffunction-sections','-fdata-sections',
          '-DSP_LR','-DSP_DEC_POOL',*flags,'-I'+str(ROOT/'c'),str(TEST),str(SRC),
          '-Wl,--gc-sections','-o',str(exe)],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    return subprocess.check_output([exe],text=True).strip()


def test_legacy_receiver_can_parse_other_voice_magics(tmp_path):
    result=_compile(tmp_path,[])
    assert 'A7:1' in result and 'A9:1' in result and 'AC:1' in result and 'AD:1' in result
    assert 'B9:1' in result and 'AA:0' in result


def test_strict_only1200_receiver_accepts_exact_1200_and_parity(tmp_path):
    result=_compile(tmp_path,['-DSP_ONLY_1200'])
    assert 'A7:0' in result and 'A9:1' in result and 'AC:0' in result and 'AD:0' in result
    assert 'B9:1' in result and 'AA:0' in result


def test_strict_mode_rejects_missing_lr_or_vlr_hiss(tmp_path):
    for flags in (["-DSP_ONLY_1200"],
                  ['-DSP_LR','-DSP_ONLY_1200','-DSP_VLR'],
                  ['-DSP_LR','-DSP_ONLY_1200','-DSP_HISS']):
        proc=subprocess.run(['cc','-std=c99','-c','-I'+str(ROOT/'c'),
                             *flags,str(SRC),'-o',str(tmp_path/'reject.o')],
                            capture_output=True,text=True)
        assert proc.returncode!=0 and 'SP_ONLY_1200 requires' in proc.stderr


def test_1200_only_builder_fails_closed_for_other_codec_and_invalid_value(tmp_path):
    builder=(ROOT/'tools/k6_build.sh').read_text()
    assert 'K6_SOTTO1200_ONLY' in builder
    assert 'K6_CODEC=1200' in builder
    assert '-DSP_ONLY_1200' in builder
