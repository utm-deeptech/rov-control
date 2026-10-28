"""The shipped controller mapping must load (catches typos in config/system.yaml before you're at the pool)."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "config"))
sys.path.insert(0, HERE)
import cfg  # noqa: E402
import gamepad as gp  # noqa: E402


def test_mapping_loads_and_idle_is_zero():
    pad = gp.Gamepad(cfg.load(os.path.join(HERE, "..", "config", "system.yaml"))["gamepad"])
    cmd, events = pad.poll()
    assert all(v == 0.0 for v in cmd.values())
    assert {ev for _, ev in pad.bind} >= {"arm", "disarm", "lights_toggle", "gain_up"}


def test_keyboard_and_expo():
    pad = gp.Gamepad(cfg.load(os.path.join(HERE, "..", "config", "system.yaml"))["gamepad"])
    pad.keys = {"W", "Q"}
    cmd, _ = pad.poll()
    assert cmd["surge"] == 1.0 and cmd["yaw"] == -1.0
    assert gp.expo(1.0, 0.3) == 1.0 and abs(gp.expo(0.5, 0.3)) < 0.5
