"""Bounded generated-source guards for experimental 256-byte centroid reads."""
from tools.k6_sotto_module import adapt,FIXED
from pathlib import Path

def test_centroid_batch_keeps_fixed_1024_byte_workspace():
    source=(FIXED/'sotto_fx_slim.c').read_text()
    assert '#define IVF_BLK 16' in source
    coded=adapt(source)
    assert '#define IVF_BLK 32' in coded
    assert '#define IVF_BLK 16' not in coded
    assert 'centroids[32*IVF_CD]' in coded
    assert '_Static_assert(sizeof(sotto_coder_work_t)<=2*NW*sizeof(int16_t)' in coded
    read=(Path(__file__).resolve().parents[1]/'c/k6/mod/sotto_tx_codec_io.h').read_text()
    assert 'first+32>' in read and 'buf,256);' in read
