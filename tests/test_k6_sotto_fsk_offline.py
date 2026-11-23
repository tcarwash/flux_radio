"""Offline Sotto 1200 XL voice-to-native-FSK transmission scheduling tests."""
import pytest
from tools.k6_sotto_fsk_offline import simulate, source_frames, RF_PACKET_S
from modem import pager


def test_real_existing_codec_wire_format_zero_loss():
    v = source_frames(375)
    result = simulate(v)
    assert result.input_frames == 375
    assert result.data_packets == 19
    assert result.packets == 19
    assert result.concealed_frames == 0
    assert result.ontime_frames == 375
    assert result.frame_values_match
    assert 0.31 < result.air_fraction < 0.34


def test_actual_pager_frame_pack_crc_whiten_sequence():
    v = source_frames(57)
    profile = pager.profile("XL", "1200", depth=1)
    encoded = profile.pack(v, msg_id=33)
    assert all(len(x) == 128 for x in encoded)
    parsed = [profile.unpack(x) for x in encoded]
    assert [x["seq"] for x in parsed] == [0, 1, 2]
    assert all(x["msg_id"] == 33 for x in parsed)
    r = pager.Assembler(profile)
    for pkt in encoded:
        r.push(pkt)
    out, good = r.frames(33)
    assert out == v and good.all()


def test_dropped_packet_results_in_800ms_concealment_without_parity():
    result = simulate(source_frames(100), drop_indices=(1,), depth=1, parity=False)
    assert result.concealed_frames == 20
    assert result.longest_concealment_ms == 800
    assert result.eventually_good_frames == 80
    assert result.frame_values_match


def test_crc_corruption_is_erasure_not_false_speech():
    result = simulate(source_frames(100), corrupt_indices=(1,), depth=1)
    assert result.crc_rejected == 1
    assert result.corrupted_packets == 1
    assert result.concealed_frames == 20
    assert result.frame_values_match


def test_duplicate_packets_are_idempotent():
    r = simulate(source_frames(100), duplicate_probability=1.0)
    assert r.concealed_frames == 0
    assert r.duplicate_packets == 5
    assert r.frame_values_match


def test_jitter_with_sufficient_playout_buffer():
    r = simulate(source_frames(375), jitter_ms=150, playout_buffer_ms=160, seed=12)
    assert r.concealed_frames == 0
    assert r.frame_values_match


def test_delayed_packet_at_zero_buffer_is_concealed():
    r = simulate(source_frames(100), jitter_ms=150, playout_buffer_ms=0, seed=15)
    assert r.concealed_frames > 0
    assert r.frame_values_match


def test_xor_parity_recovers_erased_packet_with_enough_delay():
    # For depth=2, 2 data + 1 parity packet per group. A deep initial buffer
    # is required before the parity arrives, even if average bandwidth fits.
    r = simulate(source_frames(80), depth=2, parity=True,
                 playout_buffer_ms=700, drop_indices=(0,))
    assert r.parity_reconstructed_packets == 1
    assert r.concealed_frames == 0
    assert r.frame_values_match


def test_xor_parity_arrives_too_late_for_shallow_playout():
    r = simulate(source_frames(80), depth=2, parity=True,
                 playout_buffer_ms=50, drop_indices=(0,))
    assert r.concealed_frames > 0
    assert r.eventually_good_frames == 80


def test_parity_cannot_restore_two_missing_packets_same_group():
    r = simulate(source_frames(80), depth=2, parity=True,
                 playout_buffer_ms=700, drop_indices=(0, 1))
    assert r.concealed_frames == 40


@pytest.mark.parametrize("depth", [1, 2, 4, 8])
@pytest.mark.parametrize("parity", [False, True])
def test_reused_profile_roundtrip_all_interleaving(depth, parity):
    # Large enough buffer to isolate on-air data/sequence correctness from
    # the separate latency budget; depth=8 may need >1400ms buffering.
    r = simulate(source_frames(177), depth=depth, parity=parity,
                 playout_buffer_ms=2400)
    assert r.concealed_frames == 0
    assert r.frame_values_match
    assert r.eventually_good_frames == 177


def test_depth_eight_can_stall_even_with_1400ms_buffer():
    r = simulate(source_frames(177), depth=8, parity=False,
                 playout_buffer_ms=1400)
    assert r.concealed_frames > 0
    assert r.eventually_good_frames == 177


def test_existing_L_packet_profile_reduces_codec_collection_delay():
    v = source_frames(375)
    xl = simulate(v, payload="XL", playout_buffer_ms=160)
    small = simulate(v, payload="L", playout_buffer_ms=160)
    assert small.payload_profile == "L"
    assert small.data_packets == 47
    assert small.concealed_frames == 0
    assert small.frame_values_match
    assert small.capture_to_playout_ms < xl.capture_to_playout_ms
    assert small.air_fraction < 1.0


def test_existing_L_packet_loss_conceals_320ms_not_800ms():
    r = simulate(source_frames(100), payload="L", drop_indices=(1,))
    assert r.concealed_frames == 8
    assert r.longest_concealment_ms == 320


def test_frame_timing_matches_measured_interval():
    assert abs(RF_PACKET_S - 0.26005233333333333) < 1e-10


@pytest.mark.parametrize("data", [b"", b"hi"])
def test_reject_non_sotto_slots(data):
    with pytest.raises(ValueError):
        simulate(data)


def test_soto_name_is_not_phy():
    # Guard against confusing compressed voice codec and native 2-FSK modem.
    assert pager.CODECS["1200"] == (pager.MAGIC_1200, 0.040)
