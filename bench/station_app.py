#!/usr/bin/env python3
"""ROV bench station as a desktop app: the ground station + its dashboard in one native window (no browser).

    .venv/bin/python bench/station_app.py --vehicle 192.168.0.103

Same thing as groundstation.py + http://localhost:8080, but one program: closing the window disarms the
vehicle and stops everything. The PS4/PS3 pad and the keyboard (click the window first) work as before.
"""
import argparse
import os
import socket
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-background-timer-throttling")

import groundstation as gs  # noqa: E402  (also puts topside/ and protocol/ on the path)
from http.server import ThreadingHTTPServer  # noqa: E402

from PySide6.QtCore import QUrl  # noqa: E402
from PySide6.QtGui import QIcon  # noqa: E402
from PySide6.QtWebEngineCore import QWebEnginePage  # noqa: E402
from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: E402
from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox  # noqa: E402


def free_port(preferred):
    for port in (preferred, 0):
        s = socket.socket()
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)   # same as the HTTP server: TIME_WAIT is fine
        try:
            s.bind(("127.0.0.1", port))
            return s.getsockname()[1]
        except OSError:
            continue
        finally:
            s.close()
    raise SystemExit("no free local port")


class Window(QMainWindow):
    def __init__(self, url, station):
        super().__init__()
        self.station = station
        self.setWindowTitle(f"ROV station — vehicle {station.dest[0]}")
        self.setWindowIcon(QIcon.fromTheme("applications-engineering"))
        self.view = QWebEngineView(self)
        self.view.page().javaScriptConsoleMessage = self._console
        self.view.load(QUrl(url))
        self.setCentralWidget(self.view)
        self.resize(1500, 950)

    @staticmethod
    def _console(level, msg, line, src):
        if level >= QWebEnginePage.JavaScriptConsoleMessageLevel.WarningMessageLevel:
            print(f"page: {msg} (line {line})", file=sys.stderr)

    def closeEvent(self, e):
        if self.station.armed() and QMessageBox.question(
                self, "Quit", "The vehicle is ARMED. Disarm and quit?") != QMessageBox.StandardButton.Yes:
            e.ignore()
            return
        e.accept()


def main():
    c = gs.cfgmod.load()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--vehicle", default=c["network"]["pi_ip"], help="IP of the Pi / Jetson running pi/bridge.py")
    ap.add_argument("--control-port", type=int, default=c["network"]["control_port"])
    ap.add_argument("--telem-port", type=int, default=c["network"]["telemetry_port"])
    a = ap.parse_args()

    # Two stations would both grab the pad and fight over the telemetry port (SO_REUSEADDR lets both bind).
    other = []
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            argv = open(f"/proc/{pid}/cmdline", "rb").read().split(b"\0")
        except OSError:
            continue
        if int(pid) != os.getpid() and b"python" in os.path.basename(argv[0]) and len(argv) > 1 \
                and os.path.basename(argv[1]) in (b"groundstation.py", b"station_app.py"):
            other.append(pid)
    if other:
        raise SystemExit(f"another ground station is running (pid {', '.join(other)}): close it first (kill -9 {' '.join(other)})")
    st = gs.Station(a.vehicle, a.control_port, a.telem_port, c)
    threading.Thread(target=st.run, name="link", daemon=True).start()
    port = free_port(8080)
    srv = ThreadingHTTPServer(("127.0.0.1", port), gs.make_handler(st))
    threading.Thread(target=srv.serve_forever, name="http", daemon=True).start()

    app = QApplication(sys.argv)
    app.setApplicationName("ROV station")
    app.setDesktopFileName("rov-station")          # dock icon + name from the launcher
    w = Window(f"http://127.0.0.1:{port}/", st)
    w.show()
    w.raise_()
    w.activateWindow()
    try:
        rc = app.exec()
    finally:
        st.send(gs.rp.arm(st.next(), False))                # leaving: disarm, sticks to neutral
        st.send(gs.rp.pilot(st.next(), st.mode, [0] * 6))
        srv.shutdown()
    return rc


if __name__ == "__main__":
    sys.exit(main())
