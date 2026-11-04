#!/usr/bin/env python3
"""Virtual UART over SWD: makes the Black Pill look like a serial port when its UART wires aren't connected.

    ST-LINK (SWD) <-> firmware's `swdlink` byte rings in RAM <-> this script <-> pseudo-terminal /tmp/rov-swd

Point the normal Pi bridge at it and nothing above the wire changes:
    python3 bench/swd_link.py &                       # needs the firmware ELF (for the swdlink address)
    python3 pi/bridge.py --serial /tmp/rov-swd --no-camera

The chip keeps running at full speed: the debugger reads/writes RAM between instructions (no halting).
Works without sudo when the ST-LINK udev rule is installed (it is on the bench Jetson).
"""
import argparse
import logging
import os
import socket
import struct
import subprocess
import sys
import time
import tty

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
log = logging.getLogger("swd_link")

MAGIC = 0x4C445753          # 'SWDL'
HDR = 32                    # 8 words: magic in_head in_tail out_head out_tail in_size out_size host_seen
IN_HEAD, IN_TAIL, OUT_HEAD, OUT_TAIL, HOST_SEEN = 4, 8, 12, 16, 28


def symbol(elf, name):
    """Address of a data symbol from the ELF symbol table (no binutils needed)."""
    d = open(elf, "rb").read()
    shoff, = struct.unpack_from("<I", d, 0x20)
    shentsize, shnum = struct.unpack_from("<HH", d, 0x2e)
    secs = [struct.unpack_from("<10I", d, shoff + i * shentsize) for i in range(shnum)]
    for s in secs:
        if s[1] != 2:                                   # SHT_SYMTAB
            continue
        strtab = secs[s[6]][4]
        for j in range(s[5] // 16):
            nm, value, _size, info = struct.unpack_from("<IIIB", d, s[4] + j * 16)
            if nm and d[strtab + nm:d.index(b"\0", strtab + nm)].decode() == name:
                return value
    raise SystemExit(f"{name} not in {elf} — is this the v3 firmware?")


class OpenOCD:
    """OpenOCD Tcl-RPC client (commands end with 0x1A). openocd >= 0.12: read_memory / write_memory."""

    def __init__(self, port=6666, sudo=False):
        cmd = (["sudo"] if sudo else []) + [
            "openocd", "-d0", "-f", "interface/stlink.cfg", "-f", "target/stm32f4x.cfg",
            "-c", "gdb_port disabled", "-c", "telnet_port disabled", "-c", f"tcl_port {port}", "-c", "init"]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.sock = None
        for _ in range(50):
            time.sleep(0.2)
            if self.proc.poll() is not None:
                raise SystemExit("openocd exited: ST-LINK unplugged, or another openocd is running (pkill openocd)")
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
            chunk = self.sock.recv(65536)
            if not chunk:
                raise OSError("openocd closed")
            buf += chunk
        return buf[:-1].decode().strip()

    def words(self, addr, n):
        r = self.cmd(f"read_memory 0x{addr:08x} 32 {n}").split()
        if len(r) != n:
            raise OSError(f"SWD read failed: {r[:4]}")
        return [int(x, 16) for x in r]

    def read(self, addr, n):
        r = self.cmd(f"read_memory 0x{addr:08x} 8 {n}").split()
        if len(r) != n:
            raise OSError(f"SWD read failed: {r[:4]}")
        return bytes(int(x, 16) for x in r)

    def write(self, addr, data):
        self.cmd(f"write_memory 0x{addr:08x} 8 {{{' '.join(f'0x{b:02x}' for b in data)}}}")

    def write32(self, addr, v):
        self.cmd(f"write_memory 0x{addr:08x} 32 {{0x{v:08x}}}")

    def close(self):
        try:
            self.cmd("shutdown")
        except OSError:
            pass
        self.proc.terminate()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--elf", default=os.path.join(ROOT, "firmware", "build", "blackpill-sim", "rovfw.elf"))
    ap.add_argument("--link", default="/tmp/rov-swd", help="symlink to the pseudo-terminal")
    ap.add_argument("--period", type=float, default=0.004, help="poll period (s)")
    ap.add_argument("--sudo", action="store_true", help="run openocd with sudo")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

    base = symbol(a.elf, "swdlink")
    oo = OpenOCD(sudo=a.sudo)
    master, slave = os.openpty()
    tty.setraw(slave)
    os.set_blocking(master, False)
    if os.path.lexists(a.link):
        os.unlink(a.link)
    os.symlink(os.ttyname(slave), a.link)
    log.info("swdlink @ 0x%08x  <->  %s (%s)", base, a.link, os.ttyname(slave))

    st = dict(up=0, down=0, polls=0, resets=0)
    t_stat = time.monotonic()
    in_size = out_size = 0
    synced = False
    try:
        while True:
            time.sleep(a.period)
            try:
                h = oo.words(base, 8)
            except OSError as e:
                log.warning("%s", e)
                synced = False
                time.sleep(0.5)
                continue
            st["polls"] += 1
            magic, in_head, in_tail, out_head, out_tail, in_size, out_size, seen = h
            if magic != MAGIC or not in_size or not out_size or max(in_head, in_tail) >= in_size \
                    or max(out_head, out_tail) >= out_size:
                synced = False                              # firmware not running (yet), or being flashed
                continue
            if not seen or not synced:                      # first contact, or the chip rebooted
                if synced:
                    st["resets"] += 1
                    log.info("chip restarted — resyncing")
                oo.write32(base + OUT_TAIL, out_head)       # skip anything stale
                oo.write32(base + HOST_SEEN, 1)
                synced = True
                log.info("linked to firmware (in %d B, out %d B rings)", in_size, out_size)
                continue
            # chip -> pty
            if out_head != out_tail:
                if out_head > out_tail:
                    data = oo.read(base + HDR + in_size + out_tail, out_head - out_tail)
                else:
                    data = oo.read(base + HDR + in_size + out_tail, out_size - out_tail)
                    if out_head:
                        data += oo.read(base + HDR + in_size, out_head)
                oo.write32(base + OUT_TAIL, out_head)
                st["up"] += len(data)
                try:
                    os.write(master, data)
                except OSError:
                    pass                                    # nobody has the pty open: drop, like a real UART
            # pty -> chip
            free = (in_tail - in_head - 1) % in_size
            if free:
                try:
                    data = os.read(master, free)
                except (BlockingIOError, OSError):
                    data = b""
                if data:
                    first = min(len(data), in_size - in_head)
                    oo.write(base + HDR + in_head, data[:first])
                    if len(data) > first:
                        oo.write(base + HDR, data[first:])
                    oo.write32(base + IN_HEAD, (in_head + len(data)) % in_size)
                    st["down"] += len(data)
            now = time.monotonic()
            if now - t_stat >= 10:
                log.info("polls/s=%.0f  to-chip=%d B  from-chip=%d B  restarts=%d",
                         st["polls"] / (now - t_stat), st["down"], st["up"], st["resets"])
                st.update(up=0, down=0, polls=0)
                t_stat = now
    except KeyboardInterrupt:
        pass
    finally:
        oo.close()
        if os.path.islink(a.link):
            os.unlink(a.link)


if __name__ == "__main__":
    sys.exit(main())
