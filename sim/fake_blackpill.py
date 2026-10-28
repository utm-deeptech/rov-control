#!/usr/bin/env python3
"""Simulated Blackpill: implements protocol/PROTOCOL.md "Required behaviour" on a serial port.

    sim/fake_blackpill.py --serial /dev/pts/5            # pty from socat / ptypair
    sim/fake_blackpill.py --serial /dev/ttyUSB0          # real USB-UART, if you want

Prints a live status line. Ctrl+C to quit.
"""
import argparse
import os
import select
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "protocol"))
sys.path.insert(0, os.path.join(ROOT, "config"))
import rovproto as rp  # noqa: E402
import cfg as cfgmod  # noqa: E402

import serial  # noqa: E402


class FakeBlackpill:
    def __init__(self, n_thrusters, failsafe_ms=500, telem_hz=10):
        self.n = n_thrusters
        self.failsafe_s = failsafe_ms / 1000.0
        self.telem_period = 1.0 / telem_hz
        self.t0 = time.monotonic()
        self.armed = False
        self.failsafe = False
        self.pwm = [rp.PWM_CENTER] * n_thrusters
        self.lights_us = rp.LIGHTS_OFF_US
        self.rx_ok = 0
        self.rx_bad = 0
        self.last_link = None          # monotonic time of last valid HEARTBEAT/THRUSTERS/ARM
        self.tx_seq = 0
        self.parser = rp.Parser()
        self.events = []               # human-readable log lines

    def _seq(self):
        self.tx_seq = (self.tx_seq + 1) & 0xFF
        return self.tx_seq

    def uptime_ms(self, now):
        return int((now - self.t0) * 1000)

    def feed(self, data, now):
        """Process UART bytes; returns bytes to transmit back."""
        out = b""
        bad_before = self.parser.bad
        for fr in self.parser.feed(data):
            try:
                val = rp.decode_payload(fr)
            except ValueError:
                self.rx_bad += 1
                continue
            if fr.msg_id == rp.HEARTBEAT:
                self.last_link = now
            elif fr.msg_id == rp.THRUSTERS:
                if len(val) != self.n:
                    self.rx_bad += 1
                    continue
                self.last_link = now
                if self.armed:
                    self.pwm = [rp.cmd_to_pwm(v) for v in val]
            elif fr.msg_id == rp.LIGHTS:
                self.last_link = now
                self.lights_us = rp.lights_to_pwm(val)       # lights work armed or disarmed
            elif fr.msg_id == rp.ARM:
                self.armed = val
                self.failsafe = False
                self.last_link = now
                if not val:
                    self.pwm = [rp.PWM_CENTER] * self.n
                self.events.append(f"{'ARMED' if val else 'DISARMED'} by command")
                out += rp.ack(self._seq(), rp.ARM, fr.seq)
            else:
                self.rx_bad += 1     # laptop->BP direction only has 0x01..0x03
                continue
            self.rx_ok += 1
        self.rx_bad += self.parser.bad - bad_before
        return out

    def tick(self, now):
        """Failsafe check; call often."""
        if self.armed and (self.last_link is None or now - self.last_link > self.failsafe_s):
            self.armed = False
            self.failsafe = True
            self.pwm = [rp.PWM_CENTER] * self.n
            age = "never" if self.last_link is None else f"{(now - self.last_link) * 1000:.0f} ms"
            self.events.append(f"FAILSAFE: link lost (last valid frame {age} ago) -> disarmed, 1500 us")
        if not self.armed:
            self.pwm = [rp.PWM_CENTER] * self.n

    def telemetry_frame(self, now):
        t = rp.Telemetry(self.uptime_ms(now), self.armed, self.failsafe, self.rx_ok, self.rx_bad, self.lights_us, list(self.pwm))
        return rp.telemetry(self._seq(), t)

    def status(self, now):
        link = "--" if self.last_link is None else f"{(now - self.last_link) * 1000:4.0f}ms"
        state = "\x1b[41;97m ARMED  \x1b[0m" if self.armed else "\x1b[42;30m DISARM \x1b[0m"
        fs = "\x1b[1;31mFAILSAFE\x1b[0m" if self.failsafe else "        "
        pwm = " ".join(f"{p:4d}" for p in self.pwm)
        return f"[{self.uptime_ms(now) / 1000:7.1f}s] {state} {fs} link {link} ok {self.rx_ok:5d} bad {self.rx_bad:3d} | pwm {pwm} | lights {self.lights_us}"


def main():
    c = cfgmod.load()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--serial", required=True)
    ap.add_argument("--baud", type=int, default=c["serial"]["baud"])
    ap.add_argument("--thrusters", type=int, default=c["thrusters"]["count"])
    ap.add_argument("--quiet", action="store_true", help="no live line; events only")
    a = ap.parse_args()

    bp = FakeBlackpill(a.thrusters, c["safety"]["failsafe_ms"], c["rates"]["telemetry_hz"])
    ser = serial.Serial(a.serial, a.baud, timeout=0)
    fd = ser.fileno()
    print(f"fake Blackpill on {a.serial} @ {a.baud}, {a.thrusters} thrusters, "
          f"failsafe {c['safety']['failsafe_ms']} ms. Ctrl+C to quit.", flush=True)
    next_telem = time.monotonic()
    next_print = 0.0
    tty = sys.stdout.isatty() and not a.quiet
    try:
        while True:
            now = time.monotonic()
            r, _, _ = select.select([fd], [], [], max(0.0, min(0.01, next_telem - now)))
            now = time.monotonic()
            if r:
                try:
                    data = os.read(fd, 4096)
                except OSError:
                    data = b""
                if data:
                    reply = bp.feed(data, now)
                    if reply:
                        os.write(fd, reply)
            bp.tick(now)
            if now >= next_telem:
                os.write(fd, bp.telemetry_frame(now))
                next_telem += bp.telem_period
                if next_telem < now:
                    next_telem = now + bp.telem_period
            for e in bp.events:
                print(("\r\x1b[K" if tty else "") + f"[{bp.uptime_ms(now) / 1000:7.1f}s] {e}", flush=True)
            bp.events.clear()
            if tty and now >= next_print:
                sys.stdout.write("\r\x1b[K" + bp.status(now))
                sys.stdout.flush()
                next_print = now + 0.1
    except KeyboardInterrupt:
        print()


if __name__ == "__main__":
    main()
