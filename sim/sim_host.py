#!/usr/bin/env python3
"""The 'Pi + Blackpill' in one process, with no serial port or pty: works on Windows, Linux and macOS.

    laptop --UDP:control_port--> [sim_host: FakeBlackpill] --UDP--> laptop:telemetry_port

Same wire behaviour as pi/bridge.py + sim/fake_blackpill.py (the laptop learns nothing different), so the app and
the end-to-end tests run unchanged. Ctrl+C to quit.
"""
import argparse
import os
import socket
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for sub in ("protocol", "config", "sim"):
    sys.path.insert(0, os.path.join(ROOT, sub))
import cfg as cfgmod  # noqa: E402
from fake_blackpill import FakeBlackpill  # noqa: E402


def run(udp_port, telem_port, laptop_ip, n_thrusters, failsafe_ms, telem_hz, quiet=False, stop=lambda: False):
    bp = FakeBlackpill(n_thrusters, failsafe_ms, telem_hz)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", udp_port))
    sock.settimeout(0.005)
    laptop = (laptop_ip, telem_port)        # replaced by the sender's address once a packet arrives
    tty = sys.stdout.isatty() and not quiet
    next_telem = next_print = time.monotonic()
    print(f"sim host: control :{udp_port} -> telemetry :{telem_port}, {n_thrusters} thrusters, "
          f"failsafe {failsafe_ms} ms. Ctrl+C to quit.", flush=True)

    def send(data):
        try:
            sock.sendto(data, laptop)
        except OSError:
            pass                            # laptop unreachable: keep going

    while not stop():
        now = time.monotonic()
        try:
            data, addr = sock.recvfrom(2048)
        except (socket.timeout, BlockingIOError):
            data = None
        except ConnectionResetError:        # Windows: ICMP 'port unreachable' from an earlier send
            data = None
        now = time.monotonic()
        if data:
            laptop = (addr[0], telem_port)
            reply = bp.feed(data, now)
            if reply:
                send(reply)
        bp.tick(now)
        if now >= next_telem:
            send(bp.telemetry_frame(now))
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


def main():
    if os.name == "nt":
        os.system("")                       # enable ANSI colours in the Windows console
    c = cfgmod.load()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--udp-port", type=int, default=c["network"]["control_port"])
    ap.add_argument("--telem-port", type=int, default=c["network"]["telemetry_port"])
    ap.add_argument("--laptop", default=c["sim"]["laptop_ip"])
    ap.add_argument("--thrusters", type=int, default=c["thrusters"]["count"])
    ap.add_argument("--quiet", action="store_true", help="no live line; events only")
    a = ap.parse_args()
    try:
        run(a.udp_port, a.telem_port, a.laptop, a.thrusters, c["safety"]["failsafe_ms"], c["rates"]["telemetry_hz"],
            a.quiet)
    except KeyboardInterrupt:
        print()
    except OSError as e:
        sys.exit(f"cannot bind UDP port {a.udp_port}: {e} (another sim running?)")


if __name__ == "__main__":
    main()
