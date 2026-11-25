"""Safe offline checks for API31 module launch through the single-owner Hamlib bridge."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from k6_rigctld import Rig


class ModuleLink:
    def __init__(self, *, name="SOTTO 1K FX", api=31, hz=433_500_000):
        self.name, self.api, self.hz = name, api, hz
        self.calls = []
        self.tx = False
        self.idle = True

    def status(self):
        return {
            "standby_idle": self.idle, "transmitting": self.tx,
            "sleeping": False, "master": 0,
            "vfos": [{"hz": self.hz, "mode": "FM", "wide": False}],
        }

    def slot_head(self, slot):
        assert slot == 27
        hdr = bytearray(16)
        hdr[12] = self.api
        return bytes(hdr), self.name

    def run_module_cooperative(self, slot, **kwargs):
        self.calls.append((slot, kwargs))
        return {"returned": True, "result": 8}


def rig(link=None, features=("status", "ptt", "coop_cancel"), **kwargs):
    return Rig(link or ModuleLink(), features,
               allowed_module_slot=27,
               allowed_module_name="SOTTO 1K FX",
               allowed_module_frequency=433_500_000, **kwargs)


def test_api31_module_run_is_opt_in_and_returns_real_result():
    link = ModuleLink()
    assert Rig(link, ("status", "ptt", "coop_cancel")).execute(
        r"\run_module 27") == "RPRT -11\n"
    control = rig(link)
    assert control.execute(r"\run_module 27") == "RESULT 8\nRPRT 0\n"
    assert link.calls == [(27, {
        "timeout": 15.0, "allow_higher_api": True,
    })]
    assert control.execute("t") == "0\n"


def test_module_cannot_run_during_ptt_or_wrong_frequency_or_wrong_slot():
    link = ModuleLink()
    control = rig(link)
    control.ptt_token = b"12345678"
    assert control.execute(r"\run_module 27") == "RPRT -9\n"
    control.ptt_token = None
    assert control.execute(r"\run_module 26") == "RPRT -9\n"
    link.hz = 433_510_000
    assert control.execute(r"\run_module 27") == "RPRT -9\n"
    link.hz = 433_500_000
    link.idle = False
    assert control.execute(r"\run_module 27") == "RPRT -9\n"
    assert link.calls == []


def test_requires_exact_installed_name_and_api31():
    for name, level in (("SOTTO TX", 31), ("SOTTO 1K FX", 29)):
        link = ModuleLink(name=name, api=level)
        control = rig(link)
        assert control.execute(r"\run_module 27") == "RPRT -9\n"
        assert link.calls == []


def test_requires_cooperative_cancel_capability_and_radio_returned():
    link = ModuleLink()
    control = rig(link, features=("status", "ptt"))
    assert control.execute(r"\run_module 27") == "RPRT -11\n"
    assert not link.calls

    def not_returned(slot, **kwargs):
        return {"returned": False, "result": None}
    link.run_module_cooperative = not_returned
    control = rig(link)
    assert control.execute(r"\run_module 27") == "RPRT -5\n"


def test_module_result_negative_is_visible_not_labeled_as_rf_success():
    link = ModuleLink()
    link.run_module_cooperative = lambda slot, **kwargs: {
        "returned": True, "result": -51,
    }
    assert rig(link).execute(r"\run_module 27") == "RESULT -51\nRPRT 0\n"


def test_module_tx_still_on_reports_error():
    link = ModuleLink()
    def stuck(slot, **kwargs):
        link.tx = True
        return {"returned": True, "result": 8}
    link.run_module_cooperative = stuck
    assert rig(link).execute(r"\run_module 27") == "RPRT -5\n"
