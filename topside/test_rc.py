"""RC radio (USB joystick) support: axis mapping, calibration, switches, setup-wizard logic. No hardware needed."""
import copy
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "config"))
sys.path.insert(0, HERE)
import cfg  # noqa: E402
import gamepad as gp  # noqa: E402
import rcsetup  # noqa: E402

SYSTEM = os.path.join(HERE, "..", "config", "system.yaml")


class FakeJoy:
    """Stands in for pygame.joystick.Joystick: 6 axes, 8 buttons, 1 hat."""
    def __init__(self, axes=None, buttons=None):
        self.axes = list(axes or [0.0] * 6)
        self.buttons = list(buttons or [False] * 8)
        self.hat = (0, 0)

    def get_name(self): return "RadioMaster Pocket Joystick"
    def get_instance_id(self): return 7
    def get_numaxes(self): return len(self.axes)
    def get_numbuttons(self): return len(self.buttons)
    def get_numhats(self): return 1
    def get_axis(self, i): return self.axes[i]
    def get_button(self, i): return self.buttons[i]
    def get_hat(self, i): return self.hat
    def rumble(self, *a): raise NotImplementedError      # radios have no motors: must be ignored


def make(joy, mutate=None):
    g = copy.deepcopy(cfg.load(SYSTEM)["gamepad"])
    if mutate:
        mutate(g)
    pad = gp.Gamepad(g)
    pad.pad, pad.kind, pad.joy_id = joy, "rc", 7
    return pad


def test_shipped_rc_config_loads():
    pad = gp.Gamepad(cfg.load(SYSTEM)["gamepad"])
    assert set(pad.rc_axes) == {"surge", "sway", "heave", "yaw"}
    assert {ev for _, ev, _, _ in pad._rc_binds} >= {"arm", "disarm", "lights_toggle"}
    assert pad.mode == "auto"


def test_bad_rc_config_is_rejected():
    with pytest.raises(SystemExit):
        make(FakeJoy(), lambda g: g["rc"]["axes"].update(roll_left={"axis": 0}))
    with pytest.raises(SystemExit):
        make(FakeJoy(), lambda g: g["rc"]["buttons"].update(arm_switch={"nonsense": 1}))
    with pytest.raises(SystemExit):
        make(FakeJoy(), lambda g: g.update(input="radio"))


def test_sticks_map_to_motion_and_idle_is_zero():
    joy = FakeJoy()
    pad = make(joy)
    cmd, _ = pad.poll()
    assert all(v == 0.0 for v in cmd.values())
    joy.axes[1] = -1.0          # right stick forward = elevator axis negative (default has invert: true)
    joy.axes[0] = 1.0           # right stick right
    joy.axes[2] = 1.0           # throttle up
    joy.axes[3] = -1.0          # rudder left
    cmd, _ = pad.poll()
    assert cmd["surge"] == 1.0 and cmd["sway"] == 1.0 and cmd["heave"] == 1.0 and cmd["yaw"] == -1.0


def test_deadzone_and_calibration():
    # a throttle that rests at the middle of a 0.1..0.9 range, not exactly 0
    def cal(g):
        g["rc"]["axes"]["heave"].update(min=-0.8, center=0.1, max=0.9)
    joy = FakeJoy(axes=[0, 0, 0.1, 0, 0, 0])
    pad = make(joy, cal)
    assert pad.poll()[0]["heave"] == 0.0                 # calibrated centre = zero
    joy.axes[2] = 0.9
    assert pad.poll()[0]["heave"] == pytest.approx(1.0)
    joy.axes[2] = -0.8
    assert pad.poll()[0]["heave"] == pytest.approx(-1.0)
    joy.axes[2] = 0.1 + 0.02                             # inside the deadzone
    assert pad.poll()[0]["heave"] == 0.0
    assert gp.Gamepad.rc_normalize(5.0, {"min": -1, "center": 0, "max": 1}) == 1.0     # clamped


def test_arm_switch_arms_and_disarms_and_ignores_state_at_connect():
    joy = FakeJoy(axes=[0, 0, 0, 0, 1.0, 0])             # arm switch already ON when the radio connects
    pad = make(joy)
    assert pad.poll()[1] == []                           # must NOT arm by itself
    joy.axes[4] = -1.0                                   # flip OFF -> disarm
    assert pad.poll()[1] == ["disarm"]
    assert pad.poll()[1] == []
    joy.axes[4] = 1.0                                    # flip ON -> arm
    assert pad.poll()[1] == ["arm"]
    assert pad.poll()[1] == []                           # held ON: no repeat


def test_buttons_hats_and_lights():
    joy = FakeJoy()

    def binds(g):
        g["rc"]["buttons"].update(gain_up={"button": 2}, lights_up={"hat": 0, "dir": "up"},
                                  lights_toggle={"axis": 5, "above": 0.5})
    pad = make(joy, binds)
    assert pad.poll()[1] == []
    joy.buttons[2] = True
    joy.hat = (0, 1)
    joy.axes[5] = 1.0
    assert sorted(pad.poll()[1]) == ["gain_up", "lights_toggle", "lights_up"]
    assert pad.poll()[1] == []


def test_missing_inputs_do_not_crash():
    joy = FakeJoy(axes=[0.0, 0.0])                       # a radio with only 2 axes
    pad = make(joy)
    cmd, events = pad.poll()
    assert cmd["heave"] == 0.0 and events == []


def test_null_values_from_wizard_override_defaults():
    def wizard_wrote(g):
        g["rc"]["axes"]["surge"] = {"axis": 3, "invert": False, "min": None, "center": None, "max": None}
        g["rc"]["buttons"]["arm_switch"] = {"button": 5, "axis": None, "above": None, "below": None, "hat": None}
    joy = FakeJoy()
    pad = make(joy, wizard_wrote)
    assert pad.rc_axes["surge"] == {"axis": 3, "invert": False}
    joy.axes[3] = 1.0
    joy.buttons[5] = False
    pad.poll()
    joy.buttons[5] = True
    cmd, events = pad.poll()
    assert cmd["surge"] == 1.0 and events == ["arm"]
    joy.buttons[5] = False
    assert pad.poll()[1] == ["disarm"]


def test_controller_view_axes_and_name():
    joy = FakeJoy()
    pad = make(joy)
    joy.axes[2] = 1.0
    pad.poll()
    assert pad.axes["lefty"] == -1.0 and pad.name == "RadioMaster Pocket Joystick"
    pad.rumble(1, 100)                                   # must not raise


def test_keyboard_mode_never_opens_a_radio():
    g = copy.deepcopy(cfg.load(SYSTEM)["gamepad"])
    g["input"] = "keyboard"
    pad = gp.Gamepad(g)
    assert pad._open_first() is False


# ---------- wizard logic ----------
def test_pick_axis_finds_the_moved_axis_and_direction():
    base = [0.0, 0.0, 0.0, 0.0]
    assert rcsetup.pick_axis(base, [0.02, 0.05, -0.9, 0.1]) == (2, pytest.approx(-0.9))
    assert rcsetup.pick_axis(base, [0.1, 0.2, 0.1, 0.0]) is None                   # noise only
    assert rcsetup.pick_axis(base, [0.0, 0.0, -0.9, 0.6], exclude={2}) == (3, pytest.approx(0.6))


def test_calibration_and_stick_entry():
    cal = rcsetup.calibration([-1.0, 0.0, -0.1], [1.0, 0.2, 0.1], [0.02, 0.1, 0.0])
    assert cal[0] == {"min": -1.0, "center": 0.02, "max": 1.0}
    assert cal[1] is None                                # barely moved: don't trust it
    e = rcsetup.stick_entry(0, -0.9, cal)
    assert e["axis"] == 0 and e["invert"] is True and e["center"] == 0.02
    assert rcsetup.stick_entry(1, 0.9, cal)["invert"] is False


def test_switch_spec():
    base_a, base_b = [0, 0, 0, 0, -1.0, 0], [False] * 4
    s = rcsetup.switch_spec(base_a, [0, 0, 0, 0, 1.0, 0], base_b, base_b)
    assert s["axis"] == 4 and s["above"] == 0.0 and s["button"] is None
    s = rcsetup.switch_spec(base_a, [0, 0, 0, 0, -1.0, 0], base_b, [False, False, True, False])
    assert s["button"] == 2
    assert rcsetup.switch_spec(base_a, base_a, base_b, base_b) is None             # skipped
    s = rcsetup.switch_spec(base_a, [0, 0, 0, 0, -1.0, -1.0], base_b, base_b, exclude_axes={0})
    assert s["axis"] == 5 and s["below"] == -0.5
