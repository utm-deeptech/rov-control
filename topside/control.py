"""Control link: UDP to the Pi bridge. Runs in its own thread (no Qt), so video can never block it.

- THRUSTERS at rates.thrusters_hz (zeros unless the board reports armed)
- HEARTBEAT + LIGHTS at rates.heartbeat_hz (lights work armed or disarmed)
- ARM/DISARM resent every 100 ms until the matching ACK arrives (max 10 tries)
- receives TELEMETRY / ACK on telemetry_port
"""
import collections
import socket
import threading
import time

import rovproto as rp


class ControlLink:
    def __init__(self, pi_ip, control_port, telem_port, n_thrusters, thrusters_hz=50, heartbeat_hz=10):
        self.dest = (pi_ip, control_port)
        self.n = n_thrusters
        self.thr_period = 1.0 / thrusters_hz
        self.hb_period = 1.0 / heartbeat_hz
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("0.0.0.0", telem_port))
        self.sock.settimeout(0.002)
        self.t0 = time.monotonic()
        self.lock = threading.Lock()
        self.cmd = [0] * n_thrusters
        self.lights = 0                # 0..1000
        self.seq = 0
        self.telem = None
        self.telem_time = None
        self.parser = rp.Parser()
        self.pending_arm = None        # (armed, seq, tries, next_time)
        self.logq = collections.deque(maxlen=500)
        self.tx_count = 0
        self.send_errors = 0
        self._sending = True
        self._running = True
        self._thread = threading.Thread(target=self._run, name="control", daemon=True)

    # ---------- public API (thread-safe) ----------
    def start(self):
        self._thread.start()
        return self

    def stop(self, disarm=True):
        if disarm:
            self._send_now(rp.arm(self._next_seq(), False))
            self._send_now(rp.thrusters(self._next_seq(), [0] * self.n))
        self._running = False
        self._thread.join(timeout=1)
        self.sock.close()

    def set_thrusters(self, values):
        with self.lock:
            self.cmd = [int(v) for v in values]

    def set_lights(self, level):
        with self.lock:
            self.lights = max(0, min(1000, int(level)))

    def set_dest(self, pi_ip):
        """Switch link (tether / Wi-Fi) on the fly. The Pi learns our address from the next packet."""
        with self.lock:
            self.dest = (pi_ip, self.dest[1])
        self.log(f"control -> {pi_ip}:{self.dest[1]}")

    def request_arm(self, armed):
        with self.lock:
            self.pending_arm = [bool(armed), self._next_seq(), 0, 0.0]
        self.log(f"{'ARM' if armed else 'DISARM'} requested")

    def pause_sending(self, paused):
        """Test hook: stop all transmission (simulates the laptop dying) but keep receiving."""
        self._sending = not paused

    def snapshot(self):
        with self.lock:
            age = None if self.telem_time is None else time.monotonic() - self.telem_time
            return self.telem, age, list(self.cmd)

    def board_armed(self):
        t, age, _ = self.snapshot()
        return bool(t and t.armed and age is not None and age < 1.0)

    def log(self, msg):
        self.logq.append(msg)

    # ---------- internals ----------
    def _next_seq(self):
        self.seq = (self.seq + 1) & 0xFF
        return self.seq

    def _send_now(self, frame):
        try:
            with self.lock:
                dest = self.dest
            self.sock.sendto(frame, dest)
            self.tx_count += 1
        except OSError as e:
            self.send_errors += 1
            if self.send_errors % 100 == 1:
                self.log(f"UDP send error: {e} (is the tether up?)")

    def _run(self):
        next_thr = next_hb = time.monotonic()
        while self._running:
            now = time.monotonic()
            if self._sending:
                if now >= next_thr:
                    with self.lock:
                        armed = self.telem is not None and self.telem.armed
                        vals = self.cmd if armed else [0] * self.n
                    self._send_now(rp.thrusters(self._next_seq(), vals))
                    next_thr += self.thr_period
                    if next_thr < now:
                        next_thr = now + self.thr_period
                if now >= next_hb:
                    self._send_now(rp.heartbeat(self._next_seq(), int((now - self.t0) * 1000))
                                   + rp.lights(self._next_seq(), self.lights))
                    next_hb += self.hb_period
                    if next_hb < now:
                        next_hb = now + self.hb_period
                with self.lock:
                    pa = self.pending_arm
                if pa and now >= pa[3]:
                    if pa[2] >= 10:
                        self.log(f"{'ARM' if pa[0] else 'DISARM'} not acknowledged — no link to the Blackpill?")
                        with self.lock:
                            self.pending_arm = None
                    else:
                        self._send_now(rp.arm(pa[1], pa[0]))
                        pa[2] += 1
                        pa[3] = now + 0.1
            try:
                data = self.sock.recv(2048)
            except (socket.timeout, BlockingIOError):
                continue
            except OSError:
                if not self._running:
                    break
                continue
            for fr in self.parser.feed(data):
                try:
                    val = rp.decode_payload(fr)
                except ValueError:
                    continue
                if fr.msg_id == rp.TELEMETRY:
                    with self.lock:
                        prev = self.telem
                        self.telem = val
                        self.telem_time = time.monotonic()
                    if prev and prev.armed and not val.armed and val.failsafe:
                        self.log("Blackpill FAILSAFE: link lost, board disarmed. Press ARM to re-arm.")
                elif fr.msg_id == rp.ACK:
                    with self.lock:
                        pa = self.pending_arm
                        if pa and val == (rp.ARM, pa[1]):
                            self.pending_arm = None
                            self.logq.append(f"Blackpill ACK: {'armed' if pa[0] else 'disarmed'}")
