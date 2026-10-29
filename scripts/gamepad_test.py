#!/usr/bin/env python3
"""Show what the app sees from your controller (standard names, same for PS3/PS4/PS5/Xbox). Ctrl+C to quit.

PS4/PS5 over Bluetooth: hold Share + PS until the light flashes, then pair in Ubuntu Settings > Bluetooth.
PS3: plug in by USB (works wired); for Bluetooth pairing use 'sixpair' first.
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "topside"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config"))
import cfg  # noqa: E402
import gamepad as gp  # noqa: E402

pad = gp.Gamepad(cfg.load()["gamepad"])
if pad.error:
    sys.exit(pad.error)
print("waiting for a controller (plug in or pair it)...")
try:
    while True:
        cmd, events = pad.poll()
        for e in events:
            print("\nEVENT", e)
        if pad.pad and pad.kind == "rc":          # RC radio: raw numbered axes/buttons (these are the numbers config uses)
            j = pad.pad
            ax = " ".join(f"{i}={j.get_axis(i):+.2f}" for i in range(j.get_numaxes()))
            bt = [i for i in range(j.get_numbuttons()) if j.get_button(i)]
            dofs = " ".join(f"{k}={v:+.2f}" for k, v in cmd.items() if k in ("surge", "sway", "heave", "yaw"))
            print(f"\r{j.get_name()[:22]} axes: {ax} | buttons: {','.join(map(str, bt)) or '-':<8} | {dofs}   ", end="", flush=True)
        elif pad.pad:
            axes = " ".join(f"{n}={pad._axis_val(n):+.2f}" for n in gp.AXES)
            held = [n for n in gp.BUTTONS if pad.pad.get_button(pad._btn[n])]
            dofs = " ".join(f"{k}={v:+.2f}" for k, v in cmd.items() if k in ("surge", "sway", "heave", "yaw"))
            print(f"\r{axes} | held: {','.join(held) or '-':<20} | {dofs}   ", end="", flush=True)
        time.sleep(0.02)
except KeyboardInterrupt:
    print()
