#!/usr/bin/env python3
"""Bench bridge for when the Blue Pill's UART is NOT wired: laptop --UDP--> Pi --SWD (ST-LINK)--> chip RAM.

Speaks the same protocol as pi/bridge.py, so the laptop app is unchanged:
  laptop frames -> written into the firmware's swd_in[] mailbox -> processed by the real core_rx()
  chip state (swd_out[]) -> read back -> TELEMETRY frames -> laptop
ARM/DISARM are ACKed only after the chip's own armed flag has been read back as changed.

    python3 pi/swd_bridge.py            # starts "sudo openocd" itself (the ST-LINK needs root)
"""
import argparse
import logging
import os
import select
import socket
import struct
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for sub in ("protocol", "config"):
    sys.path.insert(0, os.path.join(ROOT, sub))
import cfg as cfgmod  # noqa: E402
import rovproto as rp  # noqa: E402
from camera_supervisor import Camera  # noqa: E402

log = logging.getLogger("swd_bridge")
ELF = os.path.join(ROOT, "firmware", "build", "{board}", "rovfw.elf")
FLUSH_S, TELEM_S = 0.05, 0.1


def symbols(elf):
    """Data symbol addresses from the ELF's symbol table (no arm-none-eabi-nm needed on the Pi)."""
    with open(elf, "rb") as f:
        d = f.read()
    shoff, = struct.unpack_from("<I", d, 0x20)
    shentsize, shnum = struct.unpack_from("<HH", d, 0x2e)
    secs = [struct.unpack_from("<10I", d, shoff + i * shentsize) for i in range(shnum)]
    out = {}
    for s in secs:
        if s[1] != 2:                                   # SHT_SYMTAB
            continue
        strtab = secs[s[6]][4]
        for j in range(s[5] // 16):
            name, value, _size, info = struct.unpack_from("<IIIB", d, s[4] + j * 16)
            if info & 0xF == 1 and name:                # STT_OBJECT
                out[d[strtab + name:d.index(b"\0", strtab + name)].decode()] = value
    return out


class OpenOCD:
    """Minimal OpenOCD Tcl-RPC client (port 6666, commands end with 0x1A)."""

    def __init__(self, port, target):
        self.proc = subprocess.Popen(
            ["sudo", "openocd", "-d0", "-f", "interface/stlink.cfg", "-c", "set CPUTAPID 0",
             "-f", f"target/{target}.cfg", "-c", "gdb_port disabled", "-c", "telnet_port disabled",
             "-c", f"tcl_port {port}", "-c", "init"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.sock = None
        for _ in range(50):
            time.sleep(0.2)
            if self.proc.poll() is not None:
                raise SystemExit("openocd exited: is the ST-LINK plugged in / another openocd running?")
            try:
                self.sock = socket.create_connection(("127.0.0.1", port), timeout=2)
                break
            except OSError:
                pass
        if not self.sock:
            raise SystemExit("could not reach openocd Tcl port")

    def cmd(self, c):
        self.sock.sendall(c.encode() + b"\x1a")
        buf = b""
        while not buf.endswith(b"\x1a"):
            chunk = self.sock.recv(4096)
            if not chunk:
                raise OSError("openocd closed")
            buf += chunk
        return buf[:-1].decode().strip()

    # OpenOCD 0.11 (Ubuntu 22.04) has no read_memory/write_memory: use mdw / array2mem / mww.
    def read32(self, addr, n):
        words = []
        for line in self.cmd(f"mdw 0x{addr:08x} {n}").splitlines():
            words += [int(x, 16) for x in line.split(":", 1)[1].split()]
        if len(words) != n:
            raise OSError(f"short SWD read: {words}")
        return words

    def write_bytes(self, addr, data):
        data = bytes(data) + b"\0" * (-len(data) % 4)
        words = [int.from_bytes(data[i:i + 4], "little") for i in range(0, len(data), 4)]
        init = " ".join(f"{i} 0x{w:08x}" for i, w in enumerate(words))
        self.cmd(f"array unset a; array set a {{{init}}}; array2mem a 32 0x{addr:08x} {len(words)}")

    def write32(self, addr, v):
        self.cmd(f"mww 0x{addr:08x} 0x{v:08x}")

    def close(self):
        try:
            self.cmd("shutdown")
        except OSError:
            pass
        self.proc.terminate()


def main():
    c = cfgmod.load()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--udp-port", type=int, default=c["network"]["control_port"])
    ap.add_argument("--telem-port", type=int, default=c["network"]["telemetry_port"])
    ap.add_argument("--board", default=c.get("firmware", {}).get("board", "blackpill"), help="blackpill | bluepill")
    ap.add_argument("--elf", help="default: firmware/build/<board>/rovfw.elf (run 'make' in firmware/ first)")
    ap.add_argument("--no-camera", action="store_true")
    ap.add_argument("--tcl-port", type=int, default=6666)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

    sym = symbols(a.elf or ELF.format(board=a.board))
    if "swdlink" in sym and "swd_in" not in sym:
        raise SystemExit("this is v3 firmware: use bench/swd_link.py (virtual UART over SWD) + pi/bridge.py "
                         "--serial /tmp/rov-swd instead — see README 'Bench: Black Pill v3'")
    cam = Camera(not a.no_camera)
    IN, IN_LEN, OUT = sym["swd_in"], sym["swd_in_len"], sym["swd_out"]
    oo = OpenOCD(a.tcl_port, "stm32f4x" if a.board == "blackpill" else "stm32f1x")
    n = len(oo.read32(OUT, 12))
    log.info("SWD up: mailbox in=0x%08x len=0x%08x out=0x%08x (%d words)", IN, IN_LEN, OUT, n)

    us = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    us.bind(("0.0.0.0", a.udp_port))
    parser, laptop, seq = rp.Parser(), None, 0
    last_arm = last_thr = last_hb = last_li = None   # latest of each: a batch always fits the 64-byte mailbox
    want_ack = []                                  # (armed, seq, deadline)
    t_flush = t_telem = time.monotonic()
    stats = dict(rx=0, writes=0, telem=0)
    t_stat = t_telem

    def tx(frame):
        if laptop:
            us.sendto(frame, (laptop, a.telem_port))

    try:
        while True:
            r, _, _ = select.select([us], [], [], FLUSH_S / 2)
            if r:
                data, addr = us.recvfrom(2048)
                if laptop != addr[0]:
                    laptop = addr[0]
                    log.info("laptop is %s", laptop)
                    cam.follow(laptop)
                for f in parser.feed(data):
                    stats["rx"] += 1
                    if f.msg_id == rp.ARM:
                        last_arm = f
                    elif f.msg_id == rp.THRUSTERS:
                        last_thr = f
                    elif f.msg_id == rp.HEARTBEAT:
                        last_hb = f
                    elif f.msg_id == rp.LIGHTS:
                        last_li = f
            now = time.monotonic()
            if now - t_flush >= FLUSH_S:
                t_flush = now
                batch = b""
                if last_arm:
                    want_ack.append((bool(last_arm.payload[0]), last_arm.seq, now + 1.0))
                for f in (last_arm, last_hb, last_thr, last_li):     # ARM first: arming then thrusting
                    if f:
                        batch += f.raw                                  # 8 + 11 + 19 + 9 = 47 <= 64
                last_arm = last_hb = last_thr = last_li = None
                if batch:
                    oo.write_bytes(IN, batch)
                    oo.write32(IN_LEN, len(batch))
                    stats["writes"] += 1
            if now - t_telem >= TELEM_S:
                t_telem = now
                w = oo.read32(OUT, 12)
                seq = (seq + 1) & 0xFF
                tx(rp.telemetry(seq, rp.Telemetry(w[0], bool(w[1]), bool(w[2]), w[3], w[4], w[5], w[6:12])))
                stats["telem"] += 1
                for item in list(want_ack):
                    if bool(w[1]) == item[0]:
                        seq = (seq + 1) & 0xFF
                        tx(rp.ack(seq, rp.ARM, item[1]))
                        want_ack.remove(item)
                    elif now > item[2]:
                        want_ack.remove(item)
            cam.poll()
            if now - t_stat >= 10:
                t_stat = now
                log.info("laptop=%s frames_in=%d mailbox_writes=%d telemetry_out=%d", laptop, stats["rx"],
                         stats["writes"], stats["telem"])
    except KeyboardInterrupt:
        pass
    finally:
        cam.stop()
        oo.close()


if __name__ == "__main__":
    main()
