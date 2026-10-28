#!/usr/bin/env python3
"""UDP <-> UART bridge (runs on the Pi; also runs on the laptop in sim mode).

laptop --UDP:control_port--> [bridge] --raw bytes--> serial
serial --frames validated by rovproto--> [bridge] --UDP--> laptop:telemetry_port

Single selectors loop, non-blocking, no threads. Survives the serial port disappearing.
"""
import argparse
import errno
import logging
import os
import selectors
import socket
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "protocol"))
sys.path.insert(0, os.path.join(ROOT, "config"))
import rovproto  # noqa: E402
import cfg as cfgmod  # noqa: E402
from camera_supervisor import Camera  # noqa: E402

try:
    import serial  # pyserial
except ImportError:  # pragma: no cover
    sys.exit("pyserial missing: sudo apt install python3-serial")

log = logging.getLogger("bridge")

REOPEN_S = 1.0
STATS_S = 10.0


class Bridge:
    def __init__(self, serial_dev, baud, udp_port, laptop_ip, telem_port, camera=True):
        self.camera = Camera(camera)
        self.serial_dev = serial_dev
        self.baud = baud
        self.telem_port = telem_port
        self.fallback_addr = (laptop_ip, telem_port) if laptop_ip else None
        self.laptop_addr = None
        self.sel = selectors.DefaultSelector()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("0.0.0.0", udp_port))
        self.sock.setblocking(False)
        self.sel.register(self.sock, selectors.EVENT_READ, "udp")
        self.ser = None
        self.next_open = 0.0
        self.parser = rovproto.Parser()
        self.st = dict(udp_rx=0, ser_tx=0, ser_tx_drop=0, ser_rx_frames=0, udp_tx=0, reopen=0)

    # ---- serial management ----
    def try_open(self):
        try:
            s = serial.Serial(self.serial_dev, self.baud, timeout=0, write_timeout=0)
        except (serial.SerialException, OSError) as e:
            if self.st["reopen"] % 10 == 0:
                log.warning("serial %s not available (%s); retrying every %.0fs", self.serial_dev, e, REOPEN_S)
            self.st["reopen"] += 1
            self.next_open = time.monotonic() + REOPEN_S
            return
        self.ser = s
        self.parser = rovproto.Parser()
        self.sel.register(s.fileno(), selectors.EVENT_READ, "serial")
        log.info("serial %s open @ %d", self.serial_dev, self.baud)
        self.st["reopen"] = 0

    def close_serial(self, why):
        if self.ser is None:
            return
        log.warning("serial lost: %s", why)
        try:
            self.sel.unregister(self.ser.fileno())
        except (KeyError, ValueError):
            pass
        try:
            self.ser.close()
        except Exception:
            pass
        self.ser = None
        self.next_open = time.monotonic() + REOPEN_S

    # ---- data paths ----
    def on_udp(self):
        while True:
            try:
                data, addr = self.sock.recvfrom(2048)
            except BlockingIOError:
                return
            self.st["udp_rx"] += 1
            if self.laptop_addr is None or self.laptop_addr[0] != addr[0]:
                log.info("laptop is %s", addr[0])
            self.laptop_addr = (addr[0], self.telem_port)
            self.camera.follow(addr[0])
            if self.ser is None:
                self.st["ser_tx_drop"] += 1
                continue
            try:
                n = os.write(self.ser.fileno(), data)
                self.st["ser_tx"] += 1
                if n != len(data):
                    self.st["ser_tx_drop"] += 1
            except BlockingIOError:
                self.st["ser_tx_drop"] += 1
            except OSError as e:
                self.close_serial(e)

    def on_serial(self):
        try:
            data = os.read(self.ser.fileno(), 4096)
        except BlockingIOError:
            return
        except OSError as e:
            self.close_serial(e)
            return
        if not data:
            self.close_serial("EOF")
            return
        dest = self.laptop_addr or self.fallback_addr
        for fr in self.parser.feed(data):
            self.st["ser_rx_frames"] += 1
            if dest:
                try:
                    self.sock.sendto(fr.raw, dest)
                    self.st["udp_tx"] += 1
                except OSError:
                    pass   # laptop unreachable (tether down) — keep going

    def log_stats(self):
        s = self.st
        log.info("serial=%s laptop=%s udp_rx=%d ser_tx=%d drop=%d ser_frames=%d bad=%d udp_tx=%d",
                 "up" if self.ser else "DOWN", (self.laptop_addr or self.fallback_addr or ("?",))[0],
                 s["udp_rx"], s["ser_tx"], s["ser_tx_drop"], s["ser_rx_frames"], self.parser.bad, s["udp_tx"])

    def run(self):
        try:
            self._run()
        finally:
            self.camera.stop()

    def _run(self):
        next_stats = time.monotonic() + STATS_S
        log.info("UDP listening on :%d, telemetry -> port %d (fallback %s)",
                 self.sock.getsockname()[1], self.telem_port, self.fallback_addr)
        while True:
            now = time.monotonic()
            if self.ser is None and now >= self.next_open:
                self.try_open()
            for key, _ in self.sel.select(timeout=0.2):
                if key.data == "udp":
                    self.on_udp()
                elif key.data == "serial" and self.ser is not None:
                    self.on_serial()
            self.camera.poll()
            if now >= next_stats:
                self.log_stats()
                next_stats = now + STATS_S


def main():
    c = cfgmod.load()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--serial", default=c["serial"]["device"])
    ap.add_argument("--baud", type=int, default=c["serial"]["baud"])
    ap.add_argument("--udp-port", type=int, default=c["network"]["control_port"])
    ap.add_argument("--telem-port", type=int, default=c["network"]["telemetry_port"])
    ap.add_argument("--laptop", default=c["network"]["laptop_ip"],
                    help="fallback laptop IP until one is learned from incoming packets")
    ap.add_argument("--no-camera", action="store_true", help="don't run/supervise pi/camera.sh")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    try:
        Bridge(a.serial, a.baud, a.udp_port, a.laptop, a.telem_port, not a.no_camera).run()
    except KeyboardInterrupt:
        pass
    except OSError as e:
        if e.errno == errno.EADDRINUSE:
            sys.exit(f"UDP port {a.udp_port} already in use (another bridge running?)")
        raise


if __name__ == "__main__":
    main()
