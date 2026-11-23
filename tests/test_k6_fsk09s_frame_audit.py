"""Offline regression checks for FSK-09S fixed-baud forensic decoding."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "fsk09s_frame_audit", ROOT / "tools" / "k6_fsk09s_frame_audit.py"
)
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


class TestFsk09sFrameAudit(unittest.TestCase):
    def test_all_expected_frames_are_word_aligned(self):
        self.assertEqual([[len(p) for p in audit.expected_frames(x)] for x in "cdef"],
                         [[98, 98], [98], [128], [120]])

    def test_strict_valid_does_not_become_artifact_pass(self):
        original = audit.expected_frames("d")[0]
        score = audit.score_payload(original, original)
        self.assertEqual(score["strict_bit_errors"], 0)
        self.assertTrue(score["strict_complete_frame_pass"])
        self.assertFalse(score["posthoc_0x00f8_exact"])

    def test_artifact_displaces_last_word_without_other_errors(self):
        expected = audit.expected_frames("f")[0]
        got = bytes.fromhex("f800") + expected[:-2]
        score = audit.score_payload(got, expected)
        self.assertGreater(score["strict_bit_errors"], 0)
        self.assertFalse(score["strict_complete_frame_pass"])
        self.assertTrue(score["posthoc_0x00f8_exact"])
        self.assertTrue(score["posthoc_initial_48_words_exact"])
        self.assertTrue(score["posthoc_appended_words_exact"])
        self.assertEqual(score["posthoc_recovered_appended_words"], 11)
        self.assertTrue(score["final_declared_word_displaced_by_artifact"])
        self.assertTrue(score["posthoc_only_not_original_test_pass"])

    def test_fourbatch_has_15_inflight_words_in_displaced_frame(self):
        expected = audit.expected_frames("e")[0]
        got = bytes.fromhex("f800") + expected[:-2]
        score = audit.score_payload(got, expected)
        self.assertEqual(score["posthoc_recovered_appended_words"], 15)

    def test_mismatch_is_not_reported_as_exact(self):
        expected = audit.expected_frames("e")[0]
        received = bytearray(bytes.fromhex("f800") + expected[:-2])
        received[34] ^= 1
        score = audit.score_payload(bytes(received), expected)
        self.assertEqual(score["posthoc_leading_0x00f8_bit_errors"], 1)
        self.assertFalse(score["posthoc_0x00f8_exact"])

    def test_declared_length_is_enforced(self):
        with self.assertRaises(ValueError):
            audit.bit_errors(b"123", b"12")


if __name__ == "__main__":
    unittest.main()
