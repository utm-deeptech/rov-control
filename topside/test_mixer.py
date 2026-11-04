import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config"))
import cfg  # noqa: E402
from gamepad import deadzone  # noqa: E402
from mixer import Mixer  # noqa: E402

M = Mixer(cfg.load()["thrusters"]["mixing"])


def test_zero():
    assert M.mix({}) == [0] * 6


def test_surge_full():
    assert M.mix({"surge": 1.0}) == [-1000, -1000, 1000, 1000, 0, 0]   # vectored: front pair reversed


def test_normalised_never_exceeds_1000():
    out = M.mix({"surge": 1, "sway": 1, "yaw": 1, "heave": 1})
    assert max(abs(x) for x in out) == 1000


def test_gain_and_reverse():
    m = Mixer([[1, 0, 0, 0, 0, 0]] * 2, [False, True])
    assert m.mix({"surge": 1}, gain=0.5) == [500, -500]


def test_deadzone():
    assert deadzone(0.05, 0.1) == 0
    assert deadzone(1.0, 0.1) == 1.0
    assert abs(deadzone(-0.55, 0.1) + 0.5) < 1e-9
