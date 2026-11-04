#!/usr/bin/env python3
"""ROV Control (v3): the old app's proven parts + the new vehicle-controller dashboard, in one window.

Two modes, same program (so both always share the same safety code):

    .venv/bin/python bench/station_app.py            # ROV Control: full stack, FXTI tether / Wi-Fi, camera, Pi menu
    .venv/bin/python bench/station_app.py --bench    # ROV Bench: testing on the LAN (network.bench_ip), no FXTI,
                                                     #   no camera, no Pi menu.  --bench 1.2.3.4 for another board

From the old app (topside/app.py, tested end to end over the FXTI), reused as-is:
  camera: H.264 RTP on video_port via GStreamer (topside/video.py), the same VideoPanel
  link:   tether (network.pi_ip) / Wi-Fi (network.wifi_pi_ip), switchable live, same UDP ports to pi/bridge.py
  Pi:     reset / flash the Black Pill and restart the camera over SSH (topside/remote.py)
From the new station: PILOT/modes/PID/parameters (bench/groundstation.py) and the dashboard page.

Robustness: the top bar is native Qt drawn from the station's own state, so DISARM (button, Esc, Ctrl+D) works
even if the page freezes; a watchdog restarts a frozen page; the link thread never dies on a controller error;
the board's own failsafe disarms if this whole program stops.
"""
import argparse
import os
import socket
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
if not os.isatty(2):                                        # started from the launcher: keep a log to read later
    os.makedirs(os.path.join(ROOT, "logs"), exist_ok=True)
    _log = os.open(os.path.join(ROOT, "logs", "station_app.log"), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    os.dup2(_log, 1)
    os.dup2(_log, 2)
# Software rendering for the page: the GPU path crashed on this laptop (Skia shader errors -> frozen window).
# The page is light, so the CPU draws it easily. --gpu to try the GPU again. (Video is drawn by Qt, not the page.)
_flags = "--disable-background-timer-throttling"
if "--gpu" not in sys.argv:
    _flags += " --disable-gpu --disable-gpu-compositing"
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", _flags)

import groundstation as gs  # noqa: E402  (also puts topside/ and protocol/ on the path)
from http.server import ThreadingHTTPServer  # noqa: E402

from PySide6.QtCore import Qt, QTimer, QUrl  # noqa: E402
from PySide6.QtGui import QIcon, QKeySequence, QShortcut  # noqa: E402
from PySide6.QtWebEngineCore import QWebEnginePage  # noqa: E402
from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: E402
from PySide6.QtWidgets import (QApplication, QComboBox, QFileDialog, QHBoxLayout, QInputDialog, QLabel,  # noqa: E402
                               QMainWindow, QMenu, QMessageBox, QPushButton, QSplitter, QToolButton,
                               QVBoxLayout, QWidget)

import remote  # noqa: E402  (topside/)
from app import VideoPanel  # noqa: E402  (the old app's camera panel)
from video import VideoReceiver  # noqa: E402

PAGE_STALL_S = 3.0      # page stopped talking to us for this long -> restart its renderer

BAR_QSS = """
#bar { background: #11161d; border-bottom: 1px solid #2a3441; }
#bar QLabel { color: #d7dee8; font: 13px monospace; }
#bar QPushButton, #bar QToolButton, #bar QComboBox { padding: 5px 12px; font-weight: 600; border-radius: 6px;
    background: #222b36; color: #d7dee8; border: 1px solid #2f3a48; }
#bar QPushButton#disarm { background: #b3261e; color: white; font-size: 14px; padding: 6px 22px; border: 0; }
#bar QPushButton#disarm:disabled { background: #3a2424; color: #9b8a8a; }
"""


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


def other_stations():
    """PIDs of other ground stations (two would fight over the controller and the telemetry port)."""
    out = []
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            argv = open(f"/proc/{pid}/cmdline", "rb").read().split(b"\0")
        except OSError:
            continue
        if int(pid) != os.getpid() and b"python" in os.path.basename(argv[0]) and len(argv) > 1 \
                and (os.path.basename(argv[1]) in (b"groundstation.py", b"station_app.py")
                     or argv[1].endswith(b"topside/app.py")):
            out.append(pid)
    return out


class Window(QMainWindow):
    def __init__(self, url, station, cfg, links, link_name, bench):
        super().__init__()
        self.st, self.c, self.bench = station, cfg, bench
        self.app_name = "ROV Bench" if bench else "ROV Control"
        self.url = QUrl(url)
        self.links, self.link_name = links, link_name
        self.t_reload = 0.0
        self.reloads = 0
        self.job = None
        self.setWindowIcon(QIcon.fromTheme("applications-engineering"))

        # ---------- native bar ----------
        bar = QWidget(objectName="bar")
        h = QHBoxLayout(bar)
        h.setContentsMargins(10, 6, 10, 6)
        self.state = QLabel()
        self.state.setTextFormat(Qt.TextFormat.RichText)
        self.cmb_link = QComboBox()
        self.cmb_link.setToolTip("Which link to drive the ROV over (the FXTI tether for the real thing)")
        self._fill_links()
        self.cmb_link.activated.connect(self._link_chosen)
        pi_btn = QToolButton(text="Pi ▾", popupMode=QToolButton.ToolButtonPopupMode.InstantPopup)
        m = QMenu(pi_btn)
        self.act_reset = m.addAction("Reset Black Pill", self._reset)
        self.act_flash = m.addAction("Flash Black Pill firmware…", self._flash)
        self.act_cam = m.addAction("Restart camera", self._restart_cam)
        pi_btn.setMenu(m)
        self.video_btn = QPushButton("Video (V)", checkable=True, checked=True)
        self.video_btn.toggled.connect(self._show_video)
        reload_btn = QPushButton("Reload page (F5)")
        reload_btn.clicked.connect(lambda: self.reload("by hand"))
        self.disarm_btn = QPushButton("DISARM  (Esc)", objectName="disarm")
        self.disarm_btn.clicked.connect(self.disarm)
        h.addWidget(self.state, 1)
        if bench:
            tag = QLabel("BENCH MODE · no FXTI, no camera")
            tag.setStyleSheet("color:#ffb44c; font-weight:600;")
            h.addWidget(tag)
        for wdg in ((self.cmb_link, reload_btn, self.disarm_btn) if bench else
                    (self.cmb_link, pi_btn, self.video_btn, reload_btn, self.disarm_btn)):
            h.addWidget(wdg)
        bar.setStyleSheet(BAR_QSS)

        # ---------- camera (old app) + dashboard (new) ----------
        self.vpanel = VideoPanel()
        self.video = VideoReceiver(cfg["network"]["video_port"], cfg["video"]["stall_timeout_s"], self)
        self.video.frame.connect(self.vpanel.set_image)
        self.video.log.connect(self.st.say)
        self.view = QWebEngineView()
        self.view.page().javaScriptConsoleMessage = self._console
        self.view.page().renderProcessTerminated.connect(self._renderer_died)
        self.view.load(self.url)
        self.split = QSplitter(Qt.Orientation.Horizontal)
        self.split.addWidget(self.vpanel)
        self.split.addWidget(self.view)
        self.vpanel.setVisible(not bench)
        self.split.setStretchFactor(0, 2)
        self.split.setStretchFactor(1, 3)

        root = QWidget()
        v = QVBoxLayout(root)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(bar)
        v.addWidget(self.split, 1)
        self.setCentralWidget(root)
        self.resize(1800, 1000)
        self._update_title()

        keys = [("Esc", self.disarm), ("Ctrl+D", self.disarm), ("F5", lambda: self.reload("by hand"))]
        if not bench:
            keys.append(("V", lambda: self.video_btn.toggle()))
        for key, fn in keys:
            QShortcut(QKeySequence(key), self, fn, context=Qt.ShortcutContext.ApplicationShortcut)

        if not bench:                                           # bench: don't even open the video port
            self.video.start()
        self.timer = QTimer(self, interval=200, timeout=self.tick)
        self.timer.start()

    # ---------- link (FXTI tether / Wi-Fi / bench) ----------
    def _fill_links(self):
        self.cmb_link.clear()
        for name, ip in self.links.items():
            self.cmb_link.addItem(f"{name.upper()} · {ip}", name)
        self.cmb_link.addItem("Set bench IP…" if self.bench else "Set Wi-Fi Pi IP…", "_edit")
        self.cmb_link.setCurrentIndex(list(self.links).index(self.link_name))

    def _link_chosen(self, i):
        name = self.cmb_link.itemData(i)
        if name == "_edit":
            key, what = ("bench", "network.bench_ip") if self.bench else ("wifi", "network.wifi_pi_ip")
            ip, ok = QInputDialog.getText(self, "Link", "IP of the Pi/Jetson running pi/bridge.py ('hostname -I'):",
                                          text=self.links.get(key, ""))
            if not (ok and ip.strip()):
                self._fill_links()
                return
            self.links[key] = ip.strip()
            gs.cfgmod.save_local(what, self.links[key])
            name = key
            self.link_name = None                               # force the switch below
        if name != self.link_name:
            self.link_name = name
            self.st.set_dest(self.links[name])                 # disarms first if armed
            if name in ("tether", "wifi"):
                gs.cfgmod.save_local("network.default_link", name)
        self._fill_links()
        self._update_title()

    def _update_title(self):
        self.setWindowTitle(f"{self.app_name} — {self.link_name.upper()} {self.links[self.link_name]}")
        on_pi = self.link_name in ("tether", "wifi")            # the SSH actions are for the real Pi
        for a in (self.act_reset, self.act_flash, self.act_cam):
            a.setEnabled(on_pi)
            a.setToolTip("" if on_pi else "bench link: use bench/jetson_bench.sh on the Jetson")

    # ---------- Pi actions (old app) ----------
    def _ssh(self):
        return f"{self.c['pi']['user']}@{self.links[self.link_name]}"

    def _start_job(self, job):
        if self.job and self.job.isRunning():
            self.st.say("another Pi action is still running")
            return
        self.job = job
        job.line.connect(self.st.say)
        job.done.connect(lambda ok, title: self.st.say(f"{title}: {'OK' if ok else 'FAILED'}"))
        self.st.say(f"Pi: {job.title} …")
        job.start()

    def _reset(self):
        if self.st.armed():
            self.disarm()
        self._start_job(remote.reset_job(self._ssh(), self))

    def _flash(self):
        if self.st.armed():
            QMessageBox.warning(self, "Flash", "Refusing to flash while ARMED. Disarm first.")
            return
        path, _ = QFileDialog.getOpenFileName(self, "Black Pill firmware", os.path.join(ROOT, "firmware", "build"),
                                              "Firmware (*.bin);;All files (*)")
        if path:
            f = self.c["flash"]
            self._start_job(remote.flash_job(self._ssh(), path, f["remote_tmp"], f["address"], self))

    def _restart_cam(self):
        self._start_job(remote.restart_camera_job(self._ssh(), self))

    def _show_video(self, on):
        self.vpanel.setVisible(on)
        if on:
            self.video.start()
        else:
            self.video.stop()

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
        t, s = st.telem, st.status
        age = now - st.t_telem if st.t_telem else None
        live = t is not None and age is not None and age < 0.6
        if not live:
            arm = '<span style="color:#ff6b6b"><b>NO LINK to the Black Pill</b></span>'
        elif t.armed:
            arm = '<span style="color:#ff6b6b"><b>● ARMED</b></span>'
        else:
            arm = '<span style="color:#5bd18b"><b>○ disarmed</b></span>' + \
                  (' <span style="color:#ffb44c">failsafe</span>' if t.failsafe else "")
        modes = gs.rp.MODES
        mode = modes[s.mode].replace("_", " ") if s and s.mode < len(modes) else "?"
        pad = st.pad_name or "keyboard"
        link = f"{age * 1000:.0f} ms" if age is not None else "—"
        vid = "" if self.bench else (f" &nbsp; cam {self.video.fps:.0f} fps" if self.video.has_signal()
                                     else " &nbsp; cam: no video")
        self.state.setText(f"{arm} &nbsp; <b>{mode}</b> &nbsp; telem {link}{vid} &nbsp; 🎮 {pad}"
                           + (f" &nbsp; <span style='color:#8a96a6'>page restarts {self.reloads}</span>"
                              if self.reloads else ""))
        self.disarm_btn.setEnabled(bool(live and t.armed) or st.pending_arm is not None)
        if not self.bench:
            self._tick_video()
        # page watchdog: the page posts /input every 50 ms; silence means its renderer is stuck
        if self.isVisible() and not self.isMinimized() and st.keys_t and now - st.keys_t > PAGE_STALL_S \
                and now - self.t_reload > 10:
            self.recover(f"page silent for {now - st.keys_t:.1f} s")

    def _tick_video(self):
        """Camera panel, same as the old app."""
        self.vpanel.signal = bool(self.video.has_signal())
        self.vpanel.msg = "NO VIDEO" if self.video.available else "NO VIDEO (GStreamer missing)"
        img = self.vpanel.img
        self.vpanel.hud = f"{img.width()}x{img.height()}  {self.video.fps:.0f} fps" if img is not None else ""
        self.vpanel.update()

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
        self.st.say(f"app: page renderer stopped ({status.name}, {code}): restarting it")
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
        self.video.stop()
        e.accept()


def main():
    c = gs.cfgmod.load()
    net = c["network"]
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bench", nargs="?", const=net.get("bench_ip", ""), default=None, metavar="IP",
                    help="ROV Bench mode: LAN testing, no FXTI/camera (IP default: network.bench_ip)")
    ap.add_argument("--vehicle", help=argparse.SUPPRESS)                      # old spelling of --bench IP
    ap.add_argument("--control-port", type=int, default=net["control_port"])
    ap.add_argument("--telem-port", type=int, default=net["telemetry_port"])
    ap.add_argument("--gpu", action="store_true", help="draw the page with the GPU (crashed here: off by default)")
    a = ap.parse_args()

    bench = a.bench if a.bench is not None else a.vehicle
    app = QApplication(sys.argv)
    app.setApplicationName("ROV Bench" if bench is not None else "ROV Control")
    app.setDesktopFileName("rov-bench" if bench is not None else "rov-station")   # dock icon + name
    other = other_stations()
    if other:
        msg = f"Another ROV app is already running (pid {', '.join(other)}).\n" \
              "Switch to its window, or close it first: two would fight over the controller and the link."
        QMessageBox.warning(None, "ROV Control", msg)
        raise SystemExit(msg)

    if bench is not None:                                      # testing: only the LAN board, nothing else
        if not bench:
            raise SystemExit("--bench needs an IP (or set network.bench_ip in config)")
        links, link_name = {"bench": bench}, "bench"
    else:                                                      # full stack: FXTI tether, Wi-Fi as fallback
        links = {"tether": net["pi_ip"], "wifi": net["wifi_pi_ip"]}
        link_name = net.get("default_link") if net.get("default_link") in links else "tether"

    st = gs.Station(links[link_name], a.control_port, a.telem_port, c)
    threading.Thread(target=st.run, name="link", daemon=True).start()
    port = free_port(8080)
    srv = ThreadingHTTPServer(("127.0.0.1", port), gs.make_handler(st))
    threading.Thread(target=srv.serve_forever, name="http", daemon=True).start()

    name = "ROV%20Bench" if bench is not None else "ROV%20Control"
    w = Window(f"http://127.0.0.1:{port}/?app={name}", st, c, links, link_name, bench is not None)
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
