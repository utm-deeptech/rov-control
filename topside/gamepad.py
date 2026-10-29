"""Controller (SDL GameController: PS3 / PS4 / PS5 / Xbox, same button names) + keyboard. Poll at 50 Hz.

poll() returns (cmd dict, events list). Events: "arm", "disarm", "gain_up", "gain_down", "lights_up",
"lights_down", "lights_toggle", "connected:<name>", "disconnected".
Mapping (standard names, see config/system.yaml gamepad:) is the same for every supported pad.
"""
import os

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")         # don't let SDL open a window / fight Qt
os.environ.setdefault("SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", "1")
os.environ.setdefault("SDL_JOYSTICK_HIDAPI_PS4", "1")
os.environ.setdefault("SDL_JOYSTICK_HIDAPI_PS5", "1")

try:
    import pygame
    from pygame._sdl2 import controller as sdlc
except ImportError:  # pragma: no cover
    pygame = sdlc = None

DOFS = ("surge", "sway", "heave", "roll", "pitch", "yaw")

# Keyboard: key -> (dof, sign).  Events: SPACE disarm, [ ] gain, L lights toggle, - = lights dim/bright
KEYMAP = {
    "W": ("surge", 1), "S": ("surge", -1),
    "D": ("sway", 1), "A": ("sway", -1),
    "R": ("heave", 1), "F": ("heave", -1),
    "E": ("yaw", 1), "Q": ("yaw", -1),
}

AXES = ("leftx", "lefty", "rightx", "righty", "triggerleft", "triggerright")
BUTTONS = ("a", "b", "x", "y", "back", "guide", "start", "leftstick", "rightstick", "leftshoulder",
           "rightshoulder", "dpup", "dpdown", "dpleft", "dpright")


SDL_NAME = {"dpup": "DPAD_UP", "dpdown": "DPAD_DOWN", "dpleft": "DPAD_LEFT", "dpright": "DPAD_RIGHT"}


def deadzone(x, dz):
    if abs(x) < dz:
        return 0.0
    s = 1.0 if x > 0 else -1.0
    return s * (abs(x) - dz) / (1.0 - dz)       # rescale so output still reaches 1.0


def expo(x, e):
    """Soft centre for fine control: e=0 linear, e=1 cubic."""
    return (1.0 - e) * x + e * x * x * x


def _clean(spec):
    return {k: v for k, v in spec.items() if v is not None} if isinstance(spec, dict) else spec


class Gamepad:
    def __init__(self, gcfg):
        self.cfg = gcfg
        self.dz = float(gcfg["deadzone"])
        self.expo = float(gcfg.get("expo", 0.0))
        self.pad = None
        self.kind = None           # "gamepad" (SDL controller: PS3/PS4/PS5/Xbox) or "rc" (drone radio as USB joystick)
        self.joy_id = None
        self.mode = gcfg.get("input", "auto")        # auto | gamepad | rc | keyboard
        if self.mode not in ("auto", "gamepad", "rc", "keyboard"):
            raise SystemExit(f"config gamepad.input: '{self.mode}' is not one of auto, gamepad, rc, keyboard")
        self.rc = gcfg.get("rc") or {}
        self.keys = set()          # keyboard keys currently held (filled by the Qt app)
        self.prev = {}
        self.axes = dict.fromkeys(AXES, 0.0)    # raw stick/trigger positions, for the app's controller view
        self.held = set()                       # buttons held right now
        self.ok = False
        self.error = None
        self._scan = 0
        if pygame is None:
            self.error = "pygame not installed — keyboard only"
            return
        try:
            pygame.display.init()   # event pump (dummy video driver: no window)
            pygame.joystick.init()
            sdlc.init()
            self.ok = True
        except Exception as e:  # pragma: no cover
            self.error = f"controller init failed: {e}"
        self._axis = {n: getattr(pygame, f"CONTROLLER_AXIS_{n.upper()}") for n in AXES}
        self._btn = {n: getattr(pygame, "CONTROLLER_BUTTON_" + SDL_NAME.get(n, n.upper())) for n in BUTTONS}
        b = gcfg["buttons"]
        self.bind = []                                   # (button name, event)
        for ev in ("arm", "disarm", "gain_up", "gain_down", "lights_up", "lights_down", "lights_toggle"):
            names = b.get(ev) or []
            for n in ([names] if isinstance(names, str) else names):
                if n not in self._btn:
                    raise SystemExit(f"config gamepad.buttons.{ev}: unknown button '{n}' (use {', '.join(BUTTONS)})")
                self.bind.append((n, ev))
        for dof, a in gcfg["axes"].items():
            if a["axis"] not in self._axis:
                raise SystemExit(f"config gamepad.axes.{dof}: unknown axis '{a['axis']}' (use {', '.join(AXES)})")
        self._init_rc()

    # ---- RC transmitter (drone radio in USB joystick mode: RadioMaster, Jumper, FrSky Taranis, FlySky, ...) ----
    def _init_rc(self):
        rc = self.rc
        # config values left as null (set by rc-setup to override defaults) are ignored
        self.rc_axes = {d: {k: v for k, v in (a or {}).items() if v is not None} for d, a in (rc.get("axes") or {}).items()}
        for dof, a in self.rc_axes.items():
            if dof not in DOFS:
                raise SystemExit(f"config gamepad.rc.axes: unknown motion '{dof}' (use {', '.join(DOFS)})")
            if not isinstance(a.get("axis"), int) or a["axis"] < 0:
                raise SystemExit(f"config gamepad.rc.axes.{dof}: 'axis' must be the joystick axis number (0, 1, 2, ...)")
        self.rc_dz = float(rc.get("deadzone", 0.04))
        self.rc_expo = float(rc.get("expo", 0.0))
        # (id, event, input spec, edge): arm_switch = ON arms (refused unless the sticks are centred), OFF disarms
        self._rc_binds = []
        b = {ev: ([_clean(x) for x in v] if isinstance(v, list) else _clean(v)) for ev, v in (rc.get("buttons") or {}).items()}
        if b.get("arm_switch"):
            self._rc_binds.append(("arm_switch:on", "arm", b["arm_switch"], "rise"))
            self._rc_binds.append(("arm_switch:off", "disarm", b["arm_switch"], "fall"))
        for ev in ("arm", "disarm", "gain_up", "gain_down", "lights_up", "lights_down", "lights_toggle"):
            specs = b.get(ev) or []
            for i, spec in enumerate([specs] if isinstance(specs, dict) else specs):
                self._rc_binds.append((f"{ev}:{i}", ev, spec, "rise"))
        for _, ev, spec, _ in self._rc_binds:
            if not any(k in spec for k in ("button", "axis", "hat")):
                raise SystemExit(f"config gamepad.rc.buttons.{ev}: use {{button: N}}, {{axis: N, above: 0.5}} "
                                 f"or {{hat: N, dir: up}}")
        self._rc_prev = {}

    @staticmethod
    def rc_normalize(raw, spec):
        """Raw axis value -> -1..1 around the calibrated centre (spec min/center/max, default -1/0/1)."""
        mn, c, mx = float(spec.get("min", -1.0)), float(spec.get("center", 0.0)), float(spec.get("max", 1.0))
        if raw >= c:
            v = (raw - c) / (mx - c) if mx > c else 0.0
        else:
            v = (raw - c) / (c - mn) if c > mn else 0.0
        return max(-1.0, min(1.0, v))

    def _rc_pressed(self, spec):
        """Is this switch/button input active right now? False if the radio doesn't have that input."""
        j = self.pad
        try:
            if "button" in spec:
                return spec["button"] < j.get_numbuttons() and bool(j.get_button(spec["button"]))
            if "axis" in spec:
                if spec["axis"] >= j.get_numaxes():
                    return False
                v = j.get_axis(spec["axis"])
                if "below" in spec:
                    return v < float(spec["below"])
                return v > float(spec.get("above", 0.5))
            if "hat" in spec:
                if spec["hat"] >= j.get_numhats():
                    return False
                x, y = j.get_hat(spec["hat"])
                return {"up": y > 0, "down": y < 0, "left": x < 0, "right": x > 0}.get(spec.get("dir", "up"), False)
        except Exception:
            pass
        return False

    def _poll_rc(self, cmd, events):
        j = self.pad
        n = j.get_numaxes()
        for dof, spec in self.rc_axes.items():
            if spec["axis"] >= n:
                continue
            v = expo(deadzone(self.rc_normalize(j.get_axis(spec["axis"]), spec), self.rc_dz), self.rc_expo)
            cmd[dof] = -v if spec.get("invert") else v
        for key, ev, spec, edge in self._rc_binds:
            now = self._rc_pressed(spec)
            was = self._rc_prev.get(key)
            self._rc_prev[key] = now
            if was is None:
                continue                      # first reading after connect: a switch already ON must not fire
            if (edge == "rise" and now and not was) or (edge == "fall" and not now and was):
                events.append(ev)
        # feed the controller view in the app (same names as a gamepad): left = heave/yaw, right = surge/sway
        self.axes = dict.fromkeys(AXES, 0.0)
        self.axes.update(leftx=cmd["yaw"], lefty=-cmd["heave"], rightx=cmd["sway"], righty=-cmd["surge"])
        self.held = set()

    def rumble(self, strength=0.6, ms=200):
        """Short buzz as feedback (connect / arm / disarm). Silently ignored by pads without motors."""
        try:
            if self.pad is not None:
                self.pad.rumble(strength, strength, int(ms))
        except Exception:
            pass

    @property
    def name(self):
        if not self.pad:
            return None
        return self.pad.get_name() if self.kind == "rc" else self.pad.name

    def _open_first(self):
        if self.mode in ("auto", "gamepad"):
            for i in range(sdlc.get_count()):
                if sdlc.is_controller(i):
                    self.pad = sdlc.Controller(i)
                    self.kind = "gamepad"
                    self.prev = {n: True for n, _ in self.bind}   # ignore buttons already held at connect
                    return True
        if self.mode in ("auto", "rc") and self.rc.get("enabled", True) and self.rc_axes:
            match = str(self.rc.get("device_match") or "").lower()
            for i in range(pygame.joystick.get_count()):
                if self.mode == "auto" and sdlc.is_controller(i):
                    continue                  # a real gamepad is handled above
                joy = pygame.joystick.Joystick(i)
                if match and match not in joy.get_name().lower():
                    continue
                self.pad, self.kind, self.joy_id = joy, "rc", joy.get_instance_id()
                self._rc_prev = {}
                return True
        return False

    def _axis_val(self, name):
        v = self.pad.get_axis(self._axis[name])
        return max(-1.0, min(1.0, v / 32767.0))

    def poll(self):
        events = []
        cmd = dict.fromkeys(DOFS, 0.0)
        if self.ok:
            pygame.event.pump()
            if self.kind == "rc" and self.pad is not None:
                for e in pygame.event.get(pygame.JOYDEVICEREMOVED):
                    if getattr(e, "instance_id", None) == self.joy_id:
                        self.pad = None
            elif self.pad is not None and not self.pad.attached():
                self.pad = None
            if self.pad is None and self.kind is not None:
                self.kind = None
                events.append("disconnected")
            if self.pad is None:
                self._scan += 1
                if self._scan % 25 == 0 and self._open_first():      # look for a pad twice a second
                    events.append(f"connected:{self.name}")
        if self.pad is None:
            self.axes = dict.fromkeys(AXES, 0.0)
            self.held = set()
        elif self.kind == "rc":
            self._poll_rc(cmd, events)
        else:
            self.axes = {n: self._axis_val(n) for n in AXES}
            self.held = {n for n in BUTTONS if self.pad.get_button(self._btn[n])}
            for dof, a in self.cfg["axes"].items():
                v = expo(deadzone(self._axis_val(a["axis"]), self.dz), self.expo)
                cmd[dof] = -v if a.get("invert") else v
            for n, ev in self.bind:
                down = bool(self.pad.get_button(self._btn[n]))
                if down and not self.prev.get(n):
                    events.append(ev)
                self.prev[n] = down
        # keyboard adds on top (so it works with or without a pad)
        for k in self.keys:
            if k in KEYMAP:
                dof, s = KEYMAP[k]
                cmd[dof] = max(-1.0, min(1.0, cmd[dof] + s))
        return cmd, events
