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
if not os.isatty(2):                                        # started from the launcher: keep a log to read later
    os.makedirs(os.path.join(HERE, "..", "logs"), exist_ok=True)
    _log = os.open(os.path.join(HERE, "..", "logs", "station_app.log"), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    os.dup2(_log, 1)
    os.dup2(_log, 2)
# Software rendering: the GPU path crashed on this laptop (Skia shader errors -> frozen window). The page is
# light, so the CPU draws it easily. --gpu to try the GPU again.
_flags = "--disable-background-timer-throttling"
if "--gpu" not in sys.argv:
    _flags += " --disable-gpu --disable-gpu-compositing"
    os.environ.setdefault("QT_OPENGL", "software")
    os.environ.setdefault("QT_QUICK_BACKEND", "software")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", _flags)

import groundstation as gs  # noqa: E402  (also puts topside/ and protocol/ on the path)
from http.server import ThreadingHTTPServer  # noqa: E402

import time  # noqa: E402

from PySide6.QtCore import Qt, QTimer, QUrl  # noqa: E402
from PySide6.QtGui import QIcon, QKeySequence, QShortcut  # noqa: E402
from PySide6.QtWebEngineCore import QWebEnginePage  # noqa: E402
from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: E402
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QMainWindow, QMessageBox,  # noqa: E402
                               QPushButton, QVBoxLayout, QWidget)

PAGE_STALL_S = 3.0      # page stopped talking to us for this long -> reload it


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
    """A native Qt bar (always works) on top of the dashboard page (nice to look at, may hiccup).

    The bar is drawn by Qt from the ground station's own state, not by the web engine. So even if the page
    freezes, DISARM (button, Esc or Ctrl+D anywhere in the window) still works, and a watchdog reloads the page.
    """

    def __init__(self, url, station):
        super().__init__()
        self.st = station
        self.url = QUrl(url)
        self.t_reload = 0.0
        self.reloads = 0
        self.setWindowTitle(f"ROV station — vehicle {station.dest[0]}")
        self.setWindowIcon(QIcon.fromTheme("applications-engineering"))

        bar = QWidget(objectName="bar")
        h = QHBoxLayout(bar)
        h.setContentsMargins(10, 6, 10, 6)
        self.state = QLabel()
        self.state.setTextFormat(Qt.TextFormat.RichText)
        self.disarm_btn = QPushButton("DISARM  (Esc)")
        self.disarm_btn.setObjectName("disarm")
        self.disarm_btn.clicked.connect(self.disarm)
        reload_btn = QPushButton("Reload page  (F5)")
        reload_btn.clicked.connect(lambda: self.reload("by hand"))
        h.addWidget(self.state, 1)
        h.addWidget(reload_btn)
        h.addWidget(self.disarm_btn)
        bar.setStyleSheet("""
            #bar { background: #11161d; border-bottom: 1px solid #2a3441; }
            QLabel { color: #d7dee8; font: 13px monospace; }
            QPushButton { padding: 6px 14px; font-weight: 600; border-radius: 6px; background: #222b36; color: #d7dee8; }
            QPushButton#disarm { background: #b3261e; color: white; font-size: 14px; padding: 6px 22px; }""")

        self.view = QWebEngineView()
        self.view.page().javaScriptConsoleMessage = self._console
        self.view.page().renderProcessTerminated.connect(self._renderer_died)
        self.view.load(self.url)

        root = QWidget()
        v = QVBoxLayout(root)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(bar)
        v.addWidget(self.view, 1)
        self.setCentralWidget(root)
        self.resize(1500, 950)

        for key in ("Esc", "Ctrl+D"):                           # window-wide, even when the page has focus
            QShortcut(QKeySequence(key), self, self.disarm, context=Qt.ShortcutContext.ApplicationShortcut)
        QShortcut(QKeySequence("F5"), self, lambda: self.reload("by hand"), context=Qt.ShortcutContext.ApplicationShortcut)

        self.timer = QTimer(self, interval=200, timeout=self.tick)
        self.timer.start()

    # ---------- safety ----------
    def disarm(self):
        self.st.request_arm(False)

    def tick(self):
        try:
            self._tick()
        except Exception as e:                                  # a display glitch must never stop the timer
            print(f"bar: {e!r}", file=sys.stderr, flush=True)

    def _tick(self):
        st, now = self.st, time.monotonic()
        t = st.telem
        age = now - st.t_telem if st.t_telem else None
        live = age is not None and age < 0.6
        if not live:
            arm = '<span style="color:#ff6b6b"><b>NO LINK to the Black Pill</b></span>'
        elif t.armed:
            arm = '<span style="color:#ff6b6b"><b>● ARMED</b></span>'
        else:
            arm = '<span style="color:#5bd18b"><b>○ disarmed</b></span>' + \
                  (' <span style="color:#ffb44c">failsafe</span>' if t.failsafe else "")
        modes = gs.rp.MODES
        mode = modes[st.status.mode].replace("_", " ") if st.status and st.status.mode < len(modes) else "?"
        pad = st.pad_name or "no controller (keyboard)"
        link = f"{age * 1000:.0f} ms" if age is not None else "—"
        self.state.setText(f"{arm} &nbsp; mode <b>{mode}</b> &nbsp; telemetry {link} &nbsp; 🎮 {pad}"
                           + (f" &nbsp; <span style='color:#8a96a6'>page reloads {self.reloads}</span>" if self.reloads else ""))
        self.disarm_btn.setEnabled(bool(live and t.armed) or st.pending_arm is not None)
        # page watchdog: the page posts /input every 50 ms; silence means its renderer is stuck
        if self.isVisible() and not self.isMinimized() and st.keys_t and now - st.keys_t > PAGE_STALL_S \
                and now - self.t_reload > 10:
            self.recover(f"page silent for {now - st.keys_t:.1f} s")

    def recover(self, why):
        """A stuck renderer won't answer a reload: kill it, Qt starts a fresh one (-> _renderer_died -> reload)."""
        self.t_reload = time.monotonic()
        pid = self.view.page().renderProcessPid()
        self.st.say(f"app: page frozen ({why}): restarting its renderer")
        if pid > 0:
            try:
                os.kill(pid, 9)
                return
            except OSError:
                pass
        self.reload(why)

    def reload(self, why):
        self.t_reload = time.monotonic()
        self.reloads += 1
        self.st.say(f"app: reloading the page ({why})")
        self.view.load(self.url)

    def _renderer_died(self, status, code):
        self.st.say(f"app: page renderer stopped ({status}, {code}): restarting it")
        QTimer.singleShot(300, lambda: self.reload("renderer restarted"))

    @staticmethod
    def _console(level, msg, line, src):
        if level >= QWebEnginePage.JavaScriptConsoleMessageLevel.WarningMessageLevel:
            print(f"page: {msg} (line {line})", file=sys.stderr, flush=True)

    def closeEvent(self, e):
        if self.st.armed() and QMessageBox.question(
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
    ap.add_argument("--gpu", action="store_true", help="draw the page with the GPU (crashed here: off by default)")
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
        msg = f"Another ground station is already running (pid {', '.join(other)}).\n" \
              "Switch to its window, or close it first. Two stations would fight over the controller."
        app = QApplication(sys.argv)
        QMessageBox.warning(None, "ROV station", msg)
        raise SystemExit(msg)
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
