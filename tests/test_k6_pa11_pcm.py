"""Host checks for receive-only PA11 PCM stream and strict report parsing."""
from __future__ import annotations

import importlib.util
import json
import wave
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "c/k6/mod/pa11pcm.c"
PARSER = ROOT / "tools/k6_pa11_pcm.py"
spec = importlib.util.spec_from_file_location("k6_pa11_pcm", PARSER)
pcm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pcm)


def capture(n=24):
    data = [5500 + (i % 7) * 5 for i in range(n)]
    rows = [
        "d %04x%s" % (i, "".join(f"{s:04x}" for s in data[i:i + 12]))
        for i in range(0, n, 12)
    ]
    return {
        "ram": True, "returned": True, "result": n,
        "reports": [
            f"pa11pcm n={n} w=90 h=3 drop=4096",
            "pa11pcm r47=6140 r48=82df",
            f"pa11pcm tick100us=14 lo={min(data)} hi={max(data)}",
            *rows, f"pa11pcm end n={n}",
        ],
    }, data


def test_module_builds_with_ram_envelope():
    from modem import k6mod
    code, entry = k6mod.build(str(MODULE))
    assert entry == 0 and k6mod.source_level(str(MODULE)) == 2
    assert len(code) + 24 <= 4096


def test_source_receive_only_does_not_write_rf_or_start_tx():
    source = MODULE.read_text(encoding="utf-8")
    for forbidden in ("api->fd_write(", "tx_on(", "tx_off(", "tx_pa(", "api->tune("):
        assert forbidden not in source
    assert "api->scratch(&room)" in source
    assert "wr(GPIOA_MODER,moder)" in source
    assert "wr(GPIOA_OSPEEDR,ospeed)" in source


def test_parse_good_stream_and_timing():
    report, expected = capture()
    samples, meta = pcm.parse_capture(report)
    assert samples.tolist() == expected
    assert meta["measured_sample_rate_hz"] == pytest.approx(24 * 10000 / 14)
    assert meta["af47"] == 0x6140
    assert meta["settling_samples"] == 4096


@pytest.mark.parametrize("mutate", [
    lambda r: r["reports"].pop(-2),
    lambda r: r["reports"].__setitem__(3, r["reports"][3].replace("d 0000", "d 000c")),
    lambda r: r["reports"].__setitem__(0, "invalid header"),
    lambda r: r["reports"].__setitem__(-1, "invalid footer"),
    lambda r: r.__setitem__("returned", False),
    lambda r: r.__setitem__("result", -2),
])
def test_rejects_corrupted_or_incomplete_capture(mutate):
    report, _ = capture()
    mutate(report)
    with pytest.raises(ValueError):
        pcm.parse_capture(report)


def test_out_of_range_values_refused():
    report, _ = capture()
    report["reports"][3] = "d 0000" + "ffff" + report["reports"][3][10:]
    with pytest.raises(ValueError):
        pcm.parse_capture(report)


def test_exports_npz_and_native_rate_wav(tmp_path):
    report, data = capture(120)
    capture_path = tmp_path / "capture.json"
    capture_path.write_text(json.dumps(report), encoding="utf-8")
    out = tmp_path / "rx"
    summary = pcm.export_capture(capture_path, out)
    assert summary["n"] == 120
    assert summary["peak_to_peak_counts"] == 30
    with np.load(out.with_suffix(".npz")) as npz:
        assert npz["samples"].tolist() == data
    with wave.open(str(out.with_suffix(".wav")), "rb") as wav:
        assert wav.getframerate() == round(summary["measured_sample_rate_hz"])
        assert wav.getnframes() == 120
        assert wav.getnchannels() == 1
    assert json.loads(out.with_suffix(".meta.json").read_text())["n"] == 120
