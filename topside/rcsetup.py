"""RC radio setup wizard (RadioMaster, Jumper, Taranis, FlySky ... in USB joystick mode).

    .\rov.cmd rc-setup           (Windows)        make rc-setup           (Linux)
    "ROV Control.exe" --rc-setup                  (packaged app)

You are asked to move each stick / flip each switch; the wizard works out which joystick axis it is, which way is
positive, and the stick's min / centre / max, then saves them to config/local.yaml (gamepad.rc.*). Nothing is
guessed from the radio's model, so it works with any radio that shows up as a USB joystick.
"""
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
for sub in ("config", "topside"):
    sys.path.insert(0, os.path.join(HERE, "..", sub))
import cfg  # noqa: E402
import gamepad as gp  # noqa: E402

MIN_MOVE = 0.4          # an axis must move at least this much (of its -1..1 range) to count as "the one"
MIN_SPAN = 0.5          # a calibrated axis must span at least this much, else the calibration is ignored


# ---------- pure logic (unit-tested without a radio) ----------
def pick_axis(base, now, exclude=(), min_move=MIN_MOVE):
    """The axis that moved most from base to now: (index, signed change), or None if nothing moved enough."""
    best = None
    for i, (b, n) in enumerate(zip(base, now)):
        if i in exclude:
            continue
        d = n - b
        if abs(d) >= min_move and (best is None or abs(d) > abs(best[1])):
            best = (i, d)
    return best


def calibration(mins, maxs, centre):
    """Per-axis {min, center, max}, or None for an axis that was not moved through a sensible range."""
    out = []
    for lo, hi, c in zip(mins, maxs, centre):
        ok = hi - lo >= MIN_SPAN and lo < c < hi
        out.append({"min": round(lo, 3), "center": round(c, 3), "max": round(hi, 3)} if ok else None)
    return out


def stick_entry(index, change, cal):
    """Config entry for a stick: pushing it in the asked direction must give a POSITIVE value, so invert if it went negative."""
    e = {"axis": index, "invert": change < 0, "min": None, "center": None, "max": None}   # explicit: overrides defaults
    if cal and cal[index]:
        e.update(cal[index])
    return e


def switch_spec(base_axes, now_axes, base_buttons, now_buttons, exclude_axes=()):
    """Input that changed from base to now: a button, else an axis (threshold half way), else None."""
    blank = {"button": None, "axis": None, "above": None, "below": None, "hat": None}   # explicit: overrides defaults
    for i, (b, n) in enumerate(zip(base_buttons, now_buttons)):
        if n and not b:
            return {**blank, "button": i}
    hit = pick_axis(base_axes, now_axes, exclude=exclude_axes)
    if hit is None:
        return None
    i, d = hit
    mid = round((base_axes[i] + now_axes[i]) / 2.0, 3)
    return {**blank, "axis": i, "above": mid} if d > 0 else {**blank, "axis": i, "below": mid}


# ---------- interactive part ----------
def ensure_console():
    """A packaged windowed app has no console: open one so the wizard can talk to you."""
    if os.name == "nt" and (sys.stdin is None or not sys.stdin.isatty()) and getattr(sys, "frozen", False):
        import ctypes
        ctypes.windll.kernel32.AllocConsole()
        sys.stdout = open("CONOUT$", "w", encoding="utf-8")
        sys.stderr = sys.stdout
        sys.stdin = open("CONIN$", "r", encoding="utf-8")


def _axes(joy):
    return [joy.get_axis(i) for i in range(joy.get_numaxes())]


def _buttons(joy):
    return [bool(joy.get_button(i)) for i in range(joy.get_numbuttons())]


def _hold(pad, prompt):
    """Sample the radio until Enter is pressed. Returns (axes now, buttons now, per-axis min, per-axis max)."""
    import pygame
    done = threading.Event()
    threading.Thread(target=lambda: (input(prompt), done.set()), daemon=True).start()
    lo = hi = None
    while not done.is_set():
        pygame.event.pump()
        a = _axes(pad.pad)
        lo = a[:] if lo is None else [min(x, y) for x, y in zip(lo, a)]
        hi = a[:] if hi is None else [max(x, y) for x, y in zip(hi, a)]
        time.sleep(0.01)
    return _axes(pad.pad), _buttons(pad.pad), lo, hi


def main():
    ensure_console()
    import pygame
    c = cfg.load()
    g = dict(c["gamepad"])
    g["input"] = "rc"
    pad = gp.Gamepad(g)
    if pad.error:
        sys.exit(pad.error)
    print("RC radio setup. Connect the radio by USB and set it to 'USB Joystick (HID)' mode (EdgeTX: choose")
    print("'USB Joystick (HID)' when it asks on plugging in, or System > Hardware > USB mode). Waiting...")
    t0 = time.monotonic()
    while pad.pad is None:
        pygame.event.pump()
        if not pad._open_first():
            if time.monotonic() - t0 > 60:
                sys.exit("No joystick found after 60 s. Check the cable (data, not charge-only) and the USB mode.")
            time.sleep(0.25)
    j = pad.pad
    print(f"\nFound: {j.get_name()}  ({j.get_numaxes()} axes, {j.get_numbuttons()} buttons, {j.get_numhats()} hats)\n")

    _, _, lo, hi = _hold(pad, "Step 1/4. Move EVERY stick and switch to its full range a few times, then press Enter: ")
    base, base_b, _, _ = _hold(pad, "Step 2/4. Let go of everything: sticks centred, LEFT stick (throttle) in the MIDDLE,\n"
                                    "          all switches OFF, then press Enter: ")
    cal = calibration(lo, hi, base)

    print("\nStep 3/4. Hold each stick as asked, then press Enter.")
    asks = [("surge", "RIGHT stick fully FORWARD (up)"), ("sway", "RIGHT stick fully to the RIGHT"),
            ("heave", "LEFT stick (throttle) fully UP"), ("yaw", "LEFT stick fully to the RIGHT")]
    axes, used = {}, set()
    for dof, text in asks:
        while True:
            now, _, _, _ = _hold(pad, f"  {dof.upper():6} - hold the {text} and press Enter: ")
            hit = pick_axis(base, now, exclude=used)
            if hit:
                axes[dof] = stick_entry(hit[0], hit[1], cal)
                used.add(hit[0])
                print(f"    -> axis {hit[0]}{' (inverted)' if axes[dof].get('invert') else ''}")
                break
            print("    I did not see an axis move (or it was already used). Try again.")

    print("\nStep 4/4. Switches (press Enter without flipping to skip).")
    buttons = {}
    for key, text in (("arm_switch", "ARM switch to ON (flip it UP or DOWN, the position that should ARM)"),
                      ("lights_toggle", "LIGHTS switch/button to ON")):
        now, now_b, _, _ = _hold(pad, f"  {key} - hold the {text} and press Enter: ")
        spec = switch_spec(base, now, base_b, now_b, exclude_axes=used)
        if spec:
            buttons[key] = spec
            print("    -> " + str({k: v for k, v in spec.items() if v is not None}))
        else:
            print("    -> skipped")

    cfg.save_local("gamepad.rc.axes", axes)
    for key, spec in buttons.items():
        cfg.save_local(f"gamepad.rc.buttons.{key}", spec)
    print(f"\nSaved to {cfg.LOCAL_PATH}. Start the app: the radio is used automatically when no gamepad is plugged in")
    print("(set gamepad.input: rc in that file to always use the radio).")
    print("Safety: ARM is refused unless the sticks are centred - throttle must be in the MIDDLE. ARM switch OFF = DISARM.")


if __name__ == "__main__":
    main()
