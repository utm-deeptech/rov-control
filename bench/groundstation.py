#!/usr/bin/env python3
"""Bench ground station (protocol v3) with a live dashboard in the browser.

    laptop: [controller] -> this script --UDP--> Pi/Jetson pi/bridge.py --UART or SWD--> Black Pill
                            http://localhost:8080  (dashboard: what the board is doing, PID, mixing, params)

    .venv/bin/python bench/groundstation.py --vehicle 192.168.0.103

The topside's job (and nothing more): read the controller, apply deadzone / expo / pilot gain, turn buttons into
requests (arm, mode, lights, camera tilt) and stream PILOT at 50 Hz. Everything that makes the vehicle move —
holds, mixing, limits, failsafe — happens on the Black Pill.
Controller mapping comes from config/system.yaml (gamepad:), the same code as the real app (topside/gamepad.py).
Without a controller the dashboard's keyboard works: W/S A/D R/F Q/E, I/K camera tilt.
"""
import argparse
import collections
import json
import os
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for sub in ("protocol", "topside", "config"):
    sys.path.insert(0, os.path.join(ROOT, sub))
import cfg as cfgmod  # noqa: E402
import rovproto as rp  # noqa: E402

PILOT_HZ, HB_HZ = 50, 10
KEY_TIMEOUT = 0.4                 # browser keys count as released if the page stops reporting (tab hidden/closed)


class Station:
    def __init__(self, vehicle, cport, tport, c):
        self.c = c
        self.dest = (vehicle, cport)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("0.0.0.0", tport))
        self.sock.settimeout(0.002)
        self.lock = threading.Lock()
        self.parser = rp.Parser()
        self.seq = 0
        self.seq_lock = threading.Lock()
        g = c["gamepad"]
        self.gains = list(g.get("gains", [25, 50, 75, 100]))
        self.gain = int(g.get("default_gain", 50))
        self.lv_steps = list(c["lights"]["levels"])
        self.lights = 0
        self.gimbal = 0.0
        self.gimbal_rate = float((g.get("gimbal") or {}).get("rate", 0.6))
        self.mode = rp.MODE_MANUAL
        self.keys, self.keys_t = set(), 0.0
        self.dof = [0.0] * 6
        self.telem = self.status = None
        self.t_telem = self.t_status = None
        self.params = {}
        self.t_param = 0.0
        self.param_fetch = False
        self.pending_arm = None
        self.log = collections.deque(maxlen=80)
        self.cnt = collections.Counter()
        self.rates = {}
        self.t_rate, self.cnt_prev = time.monotonic(), collections.Counter()
        self.pad = None
        self.pad_name = None
        self.pad_axes, self.pad_held = {}, []
        self.pad_error = None
        self.pad_failed = False
        self.say("ground station up, vehicle at %s:%d" % self.dest)

    # ---------- helpers ----------
    def say(self, msg):
        self.log.append([time.strftime("%H:%M:%S"), msg])

    def next(self):
        with self.seq_lock:                       # called from the HTTP thread and the link thread
            self.seq = (self.seq + 1) & 0xFF
            return self.seq

    def send(self, frame, what=None):
        try:
            self.sock.sendto(frame, self.dest)
            self.cnt["tx"] += 1
            if what:
                self.cnt["tx_" + what] += 1
        except OSError as e:
            self.cnt["tx_err"] += 1
            if self.cnt["tx_err"] % 100 == 1:
                self.say(f"UDP send error: {e}")

    def armed(self):
        return bool(self.telem and self.telem.armed and self.t_telem and time.monotonic() - self.t_telem < 1)

    # ---------- commands (from the pad, the keyboard, or the page) ----------
    def request_arm(self, on):
        with self.lock:
            self.pending_arm = [bool(on), self.next(), 0, 0.0, time.monotonic()]
        self.say("ARM requested" if on else "DISARM requested")

    def set_mode(self, m):
        with self.lock:
            self.mode = int(m)
        self.say(f"mode -> {rp.MODES[self.mode]}")

    def step_gain(self, d):
        i = min(range(len(self.gains)), key=lambda k: abs(self.gains[k] - self.gain))
        self.gain = self.gains[max(0, min(len(self.gains) - 1, i + d))]
        self.say(f"pilot gain {self.gain}%")

    def step_lights(self, d):
        pct = self.lights / 10
        i = min(range(len(self.lv_steps)), key=lambda k: abs(self.lv_steps[k] - pct))
        self.lights = int(self.lv_steps[max(0, min(len(self.lv_steps) - 1, i + d))] * 10)

    def command(self, j):
        cmd = j.get("cmd")
        if cmd == "arm":
            self.request_arm(True)
        elif cmd == "disarm":
            self.request_arm(False)
        elif cmd == "mode":
            self.set_mode(j["mode"])
        elif cmd == "gain":
            self.step_gain(int(j["d"]))
        elif cmd == "lights":
            self.lights = max(0, min(1000, int(j["value"])))
        elif cmd == "lights_toggle":
            self.lights = 0 if self.lights else 1000
        elif cmd == "gimbal":
            self.gimbal = max(-1.0, min(1.0, float(j["value"])))
        elif cmd == "param_set":
            p = self.params.get(int(j["index"]))
            self.send(rp.param_set(self.next(), int(j["index"]), float(j["value"])), "param")
            self.say(f"set {p.name if p else j['index']} = {j['value']}")
        elif cmd == "param_save":
            self.send(rp.param_save(self.next()), "param")
            self.say("save parameters to flash")
        elif cmd == "param_reset":
            self.send(rp.param_reset(self.next()), "param")
            self.say("reset parameters to defaults (not saved yet)")
        elif cmd == "param_refresh":
            self.fetch_params()
        else:
            return False
        return True

    def fetch_params(self):
        self.param_fetch = True
        self.t_param = time.monotonic()
        self.send(rp.param_get(self.next(), 0xFF), "param")

    def set_keys(self, keys):
        with self.lock:
            self.keys = {str(k).upper() for k in keys}
            self.keys_t = time.monotonic()

    # ---------- gamepad ----------
    def open_pad(self):
        try:
            from gamepad import Gamepad
            self.pad = Gamepad(self.c["gamepad"])
            if not self.pad.ok:
                self.pad_error = self.pad.error
        except SystemExit as e:
            self.pad_error = str(e)
        except Exception as e:  # pragma: no cover
            self.pad_error = f"gamepad unavailable: {e}"
        if self.pad_error:
            self.say(self.pad_error)

    def poll_input(self, dt):
        with self.lock:
            keys = set(self.keys) if time.monotonic() - self.keys_t < KEY_TIMEOUT else set()
        cmd, events = {d: 0.0 for d in rp.DOFS}, []
        if self.pad is not None:
            self.pad.keys = keys
            try:
                cmd, events = self.pad.poll()
            except Exception as e:                          # SDL hiccup: never let it kill the link thread
                cmd, events = {d: 0.0 for d in rp.DOFS}, []
                if not self.pad_failed:
                    self.say(f"controller read failed ({e}): sticks neutral, recovering")
                    if self.armed():
                        self.request_arm(False)             # no trustworthy pilot input -> disarm
                self.pad_failed = True
                try:
                    import pygame
                    pygame.display.init()
                    pygame.joystick.init()
                except Exception:
                    pass
            else:
                if self.pad_failed:
                    self.say("controller reading again")
                self.pad_failed = False
            self.pad_name = self.pad.name
            self.pad_axes = {k: round(v, 3) for k, v in self.pad.axes.items()}
            self.pad_held = sorted(self.pad.held)
            rate = self.pad.gimbal_rate() if self.pad.name else 0.0
        else:
            for k, (dof, s) in {"W": ("surge", 1), "S": ("surge", -1), "D": ("sway", 1), "A": ("sway", -1),
                                "R": ("heave", 1), "F": ("heave", -1), "E": ("yaw", 1), "Q": ("yaw", -1)}.items():
                if k in keys:
                    cmd[dof] = max(-1.0, min(1.0, cmd[dof] + s))
            rate = 0.0
        rate += (1.0 if "I" in keys else 0.0) - (1.0 if "K" in keys else 0.0)
        self.gimbal = max(-1.0, min(1.0, self.gimbal + rate * self.gimbal_rate * dt))
        for ev in events:
            if ev == "arm":
                self.request_arm(True)
            elif ev == "disarm":
                self.request_arm(False)
            elif ev in ("gain_up", "gain_down"):
                self.step_gain(1 if ev == "gain_up" else -1)
            elif ev == "lights_up":
                self.step_lights(1)
            elif ev == "lights_down":
                self.step_lights(-1)
            elif ev == "lights_toggle":
                self.lights = 0 if self.lights else 1000
            elif ev == "mode_manual":
                self.set_mode(rp.MODE_MANUAL)
            elif ev == "mode_stabilize":
                self.set_mode(rp.MODE_STABILIZE)
            elif ev == "mode_depth_hold":
                self.set_mode(rp.MODE_DEPTH_HOLD)
            elif ev == "gimbal_center":
                self.gimbal = 0.0
            elif ev.startswith("connected:"):
                self.say(f"controller connected: {ev.split(':', 1)[1]}")
                self.pad.rumble()
            elif ev == "disconnected":
                self.say("controller DISCONNECTED")
                if self.armed():
                    self.request_arm(False)                 # never leave the vehicle armed with no pilot
        g = self.gain / 100.0
        self.dof = [max(-1.0, min(1.0, cmd.get(d, 0.0))) * g for d in rp.DOFS]

    # ---------- main loop ----------
    def run(self):
        self.open_pad()
        now = time.monotonic()
        t_pilot = t_hb = t_missing = now
        self.fetch_params()
        while True:
            try:
                t_pilot, t_hb, t_missing = self.step(t_pilot, t_hb, t_missing)
            except Exception as e:                          # log and keep the link alive, whatever happens
                self.say(f"ground station error: {e!r}")
                time.sleep(0.05)

    def step(self, t_pilot, t_hb, t_missing):
        """One pass of the link loop: pilot + heartbeat out, ARM retries, param fetch, frames in."""
        if True:
            now = time.monotonic()
            if now >= t_pilot:
                self.poll_input(1.0 / PILOT_HZ)
                self.send(rp.pilot(self.next(), self.mode, [round(x * 1000) for x in self.dof], self.lights,
                                   round(self.gimbal * 1000)), "pilot")
                t_pilot = max(t_pilot + 1.0 / PILOT_HZ, now)
            if now >= t_hb:
                self.send(rp.heartbeat(self.next(), int(now * 1000)), "hb")
                t_hb = max(t_hb + 1.0 / HB_HZ, now)
            with self.lock:
                pa = self.pending_arm
            if pa and self.telem and self.t_telem > pa[4] + 0.05 and self.telem.armed == pa[0]:
                # the board already reports the state we asked for (the ACK was lost on the way back)
                with self.lock:
                    self.pending_arm = None
                self.say("Black Pill confirms " + ("ARMED" if pa[0] else "DISARMED") + " (telemetry)")
                pa = None
            if pa and now >= pa[3]:
                if pa[2] >= 20:
                    self.say(("ARM" if pa[0] else "DISARM") + " not acknowledged: no link to the Black Pill?")
                    self.pending_arm = None
                else:
                    self.send(rp.arm(pa[1], pa[0]), "arm")
                    pa[2] += 1
                    pa[3] = now + 0.1
            # the stream can lose frames on a busy link: ask again for the ones that didn't arrive
            if self.param_fetch and now - self.t_param > 1.0 and now >= t_missing:
                total = next(iter(self.params.values())).count if self.params else 0
                missing = [i for i in range(total) if i not in self.params]
                if not total:
                    self.fetch_params()
                elif missing:
                    self.send(rp.param_get(self.next(), missing[0]), "param")
                else:
                    self.param_fetch = False
                    self.say(f"{total} parameters loaded from the Black Pill")
                t_missing = now + 0.02
            if now - self.t_rate >= 1.0:
                self.rates = {k: (self.cnt[k] - self.cnt_prev[k]) / (now - self.t_rate) for k in self.cnt}
                self.cnt_prev, self.t_rate = collections.Counter(self.cnt), now
            try:
                data = self.sock.recv(4096)
            except OSError:                             # timeout (nothing yet) or a network blip
                return t_pilot, t_hb, t_missing
            for fr in self.parser.feed(data):
                self.cnt["rx"] += 1
                self.cnt["rx_" + rp.NAMES.get(fr.msg_id, "?")] += 1
                try:
                    v = rp.decode_payload(fr)
                except ValueError:
                    continue
                self.on_frame(fr.msg_id, v)
            return t_pilot, t_hb, t_missing

    def on_frame(self, mid, v):
        now = time.monotonic()
        if mid == rp.TELEMETRY:
            prev = self.telem
            self.telem, self.t_telem = v, now
            if prev and prev.armed and not v.armed and v.failsafe:
                self.say("FAILSAFE on the Black Pill: link lost -> disarmed")
        elif mid == rp.STATUS:
            prev = self.status
            self.status, self.t_status = v, now
            if prev and prev.arm_block != v.arm_block and v.arm_block:
                self.say(f"ARM refused by the Black Pill: {rp.ARM_BLOCKS[v.arm_block]}")
            if prev and prev.mode != v.mode:
                self.say(f"Black Pill mode: {rp.MODES[v.mode]}"
                         + ("" if v.mode == v.mode_req else f" (asked {rp.MODES[v.mode_req]}: sensor missing)"))
        elif mid == rp.PARAM_VALUE:
            self.params[v.index] = v
            self.t_param = now
        elif mid == rp.RESULT:
            name = rp.NAMES.get(v[0], hex(v[0]))
            code = rp.RESULTS[v[2]] if v[2] < len(rp.RESULTS) else v[2]
            if v[0] != rp.PARAM_SET or v[2]:
                self.say(f"{name}: {code}")
        elif mid == rp.ACK:
            with self.lock:
                pa = self.pending_arm
                if pa and v == (rp.ARM, pa[1]):
                    self.pending_arm = None
                    self.say("Black Pill ACK " + ("ARM" if pa[0] else "DISARM"))

    # ---------- dashboard ----------
    def snapshot(self):
        now = time.monotonic()
        t, s = self.telem, self.status
        out = {
            "vehicle": self.dest[0],
            "rates": self.rates,
            "telem_age": None if self.t_telem is None else now - self.t_telem,
            "status_age": None if self.t_status is None else now - self.t_status,
            "telem": t.__dict__ if t else None,
            "status": None,
            "params": [p.__dict__ for _, p in sorted(self.params.items())],
            "pilot": {"mode": self.mode, "dof": self.dof, "gain": self.gain, "lights": self.lights,
                      "gimbal": self.gimbal, "pad": self.pad_name, "pad_error": self.pad_error,
                      "axes": self.pad_axes, "held": self.pad_held,
                      "keys": sorted(self.keys) if now - self.keys_t < KEY_TIMEOUT else []},
            "arm_pending": bool(self.pending_arm),
            "log": list(self.log)[-40:],
            "mapping": self.mapping(),
        }
        if s:
            d = dict(s.__dict__)
            d["flag_names"] = [f for f in rp.Status.FLAGS if s.flag(f)]
            d["mode_name"], d["mode_req_name"] = rp.MODES[s.mode], rp.MODES[s.mode_req]
            d["arm_block_text"] = rp.ARM_BLOCKS[s.arm_block] if s.arm_block < len(rp.ARM_BLOCKS) else s.arm_block
            out["status"] = d
        return out

    def mapping(self):
        g = self.c["gamepad"]
        ax = {k: v["axis"] + (" (inv)" if v.get("invert") else "") for k, v in g["axes"].items()}
        b = {k: (v if isinstance(v, str) else " / ".join(v)) for k, v in g["buttons"].items()}
        gm = g.get("gimbal") or {}
        return {"axes": ax, "buttons": b, "gimbal": {"up": gm.get("up_axis"), "down": gm.get("down_axis")}}


def make_handler(st):
    page = os.path.join(HERE, "station.html")

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                with open(page, "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            elif self.path.startswith("/state"):
                self._send(200, json.dumps(st.snapshot()).encode())
            else:
                self._send(404, b"{}")

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            try:
                j = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return self._send(400, b'{"ok":false}')
            if self.path == "/input":
                st.set_keys(j.get("keys", []))
                return self._send(200, b'{"ok":true}')
            if self.path == "/cmd":
                ok = st.command(j)
                return self._send(200 if ok else 400, json.dumps({"ok": ok}).encode())
            self._send(404, b"{}")

    return H


def main():
    c = cfgmod.load()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--vehicle", default=c["network"]["pi_ip"], help="IP of the Pi / Jetson running pi/bridge.py")
    ap.add_argument("--control-port", type=int, default=c["network"]["control_port"])
    ap.add_argument("--telem-port", type=int, default=c["network"]["telemetry_port"])
    ap.add_argument("--http", type=int, default=8080)
    a = ap.parse_args()
    st = Station(a.vehicle, a.control_port, a.telem_port, c)
    threading.Thread(target=st.run, name="link", daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", a.http), make_handler(st))
    print(f"dashboard: http://localhost:{a.http}   vehicle: {a.vehicle}:{a.control_port}   (Ctrl-C quits)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        st.send(rp.arm(st.next(), False))                   # leaving: disarm
        st.send(rp.pilot(st.next(), st.mode, [0] * 6))


if __name__ == "__main__":
    main()
