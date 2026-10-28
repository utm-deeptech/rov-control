#!/usr/bin/env python3
"""Linked pty pair — fallback for `socat -d -d pty,raw,echo=0 pty,raw,echo=0`.

Prints the two device paths on one line ("A B"), then relays bytes until killed.
"""
import os
import select
import sys
import tty


def main():
    ends = []
    for _ in range(2):
        m, s = os.openpty()
        tty.setraw(s)
        ends.append((m, s, os.ttyname(s)))
    print(ends[0][2], ends[1][2], flush=True)
    a, b = ends[0][0], ends[1][0]
    peer = {a: b, b: a}
    while True:
        r, _, _ = select.select([a, b], [], [])
        for fd in r:
            try:
                data = os.read(fd, 65536)
            except OSError:
                continue          # nobody has the slave open right now
            if data:
                try:
                    os.write(peer[fd], data)
                except OSError:
                    pass


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
