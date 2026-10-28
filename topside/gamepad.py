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


class Gamepad:
    def __init__(self, gcfg):
        self.cfg = gcfg
        self.dz = float(gcfg["deadzone"])
        self.expo = float(gcfg.get("expo", 0.0))
        self.pad = None
        self.keys = set()          # keyboard keys currently held (filled by the Qt app)
        self.prev = {}
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

    @property
    def name(self):
        return self.pad.name if self.pad else None

    def _open_first(self):
        for i in range(sdlc.get_count()):
            if sdlc.is_controller(i):
                self.pad = sdlc.Controller(i)
                self.prev = {n: True for n, _ in self.bind}   # ignore buttons already held at connect
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
            if self.pad is not None and not self.pad.attached():
                self.pad = None
                events.append("disconnected")
            if self.pad is None:
                self._scan += 1
                if self._scan % 25 == 0 and self._open_first():      # look for a pad twice a second
                    events.append(f"connected:{self.pad.name}")
        if self.pad is not None:
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
