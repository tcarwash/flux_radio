"""Loss detection for the physical IQ capture protocol, independent of RF hardware."""
from pathlib import Path
import struct
import sys
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from k6_iq_capture import decode_frames


def packet(seq,index,stamp,mode=1,ratio=8):
    return seq,struct.pack('<IIIBBH',index,stamp,4155,mode,ratio,0)+bytes([1,255,2,254])


def test_iq_continuity():
    samples,meta,check=decode_frames([packet(0,512,10000),packet(1,1536,20000)],dict(mode=1,ratio=8,outputs=4,frames=2))
    assert samples.tolist()==[1,-1,2,-2,1,-1,2,-2]
    assert meta.shape==(2,7)
    assert check==dict(sequence_gaps=0,index_gaps=0,nonmonotonic_timestamps=0,frame_count_mismatch=0,received_samples=4,missing_samples=0)


def test_dropped_output_remains_visible():
    _,_,check=decode_frames([packet(0,512,10000),packet(2,2560,30000)],dict(mode=1,ratio=8,outputs=6,frames=2))
    assert check['sequence_gaps']==check['index_gaps']==1
    assert check['missing_samples']==2


def test_timestamp_and_statistics_mismatch():
    _,_,check=decode_frames([packet(0,512,10000),packet(1,1536,9000)],dict(mode=1,ratio=8,outputs=4,frames=3))
    assert check['nonmonotonic_timestamps']==1
    assert check['frame_count_mismatch']==1


def test_payload_configuration_cannot_change_midstream():
    with pytest.raises(ValueError,match='configuration'):
        decode_frames([packet(0,512,10000,ratio=4)],dict(mode=1,ratio=8,outputs=2,frames=1))


def test_raw_population_counts_have_distinct_stride():
    _,_,check=decode_frames([packet(0,0,10000,mode=0,ratio=1),packet(1,256,20000,mode=0,ratio=1)],dict(mode=0,ratio=8,outputs=8,frames=2))
    assert check['index_gaps']==0
    assert check['missing_samples']==0
