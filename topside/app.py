#!/usr/bin/env python3
"""ROV topside app: video, gamepad/keyboard control, telemetry, Blackpill reset/flash.

    python topside/app.py          # real ROV (config/system.yaml)
    python topside/app.py --sim    # against sim/run_sim.sh on this laptop
"""
import argparse
import os
import signal
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for sub in ("protocol", "config", "topside"):
    sys.path.insert(0, os.path.join(ROOT, sub))

import cfg as cfgmod  # noqa: E402
from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, Qt, QTimer  # noqa: E402
from PySide6.QtGui import QColor, QFont, QBrush, QImage, QLinearGradient, QPainter, QPainterPath, QPen  # noqa: E402
from PySide6.QtWidgets import (QApplication, QFileDialog, QHBoxLayout, QLabel, QMainWindow,  # noqa: E402
                               QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
                               QFrame, QGridLayout, QSizePolicy, QComboBox, QSlider, QInputDialog, QDialog)

from control import ControlLink  # noqa: E402
from gamepad import Gamepad  # noqa: E402
from mixer import Mixer  # noqa: E402
import remote  # noqa: E402
from thruster_setup import ThrusterSetup  # noqa: E402
from video import VideoReceiver  # noqa: E402

# ---- deep-tech theme (palette shared with the deeptech website) ----
BG, PANEL, PANEL2, BORDER = "#060B18", "#0A1226", "#101A36", "#1B2A4F"
TEXT, MUTED = "#E2E8F0", "#64748B"
BLUE, SKY, CYAN = "#3B82F6", "#60A5FA", "#22D3EE"
GREEN, RED, AMBER, GREY = "#10B981", "#EF4444", "#F59E0B", "#334155"
UI_FONT, MONO_FONT = "Inter, 'Segoe UI', 'Noto Sans', sans-serif", "'JetBrains Mono', 'DejaVu Sans Mono', monospace"

QSS = f"""
* {{ font-family: {UI_FONT}; color: {TEXT}; }}
QMainWindow, QWidget#root {{ background: {BG}; }}
QFrame#card {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 14px; }}
QLabel {{ background: transparent; }}
QLabel#h {{ color: {SKY}; font-size: 10px; font-weight: 700; letter-spacing: 2px; }}
QLabel#title {{ font-size: 17px; font-weight: 800; letter-spacing: 4px; color: {TEXT}; }}
QLabel#sub {{ color: {MUTED}; font-size: 11px; letter-spacing: 1px; }}
QLabel#tileval {{ font-family: {MONO_FONT}; font-size: 15px; font-weight: 700; color: {TEXT}; }}
QLabel#tilekey {{ color: {MUTED}; font-size: 9px; font-weight: 700; letter-spacing: 1.5px; }}
QPushButton#action {{ background: {PANEL2}; border: 1px solid {BORDER}; border-radius: 10px;
    padding: 9px 10px; font-weight: 600; color: {TEXT}; }}
QPushButton#action:hover {{ border-color: {BLUE}; background: #14224A; }}
QPushButton#action:pressed {{ background: {BLUE}; }}
QPlainTextEdit {{ background: #040814; border: 1px solid {BORDER}; border-radius: 10px; padding: 6px;
    font-family: {MONO_FONT}; font-size: 10px; color: #9FB3D9; selection-background-color: {BLUE}; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 4px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: {BLUE}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QToolTip {{ background: {PANEL2}; color: {TEXT}; border: 1px solid {BLUE}; }}
QPushButton#chip {{ background: {PANEL2}; border: 1px solid {BORDER}; border-radius: 8px; padding: 5px 8px;
    font-family: {MONO_FONT}; font-size: 11px; font-weight: 700; color: {TEXT}; }}
QPushButton#chip:hover {{ border-color: {BLUE}; }}
QPushButton#chip:checked {{ background: {AMBER}; color: #111; border-color: {AMBER}; }}
QComboBox {{ background: {PANEL2}; border: 1px solid {BORDER}; border-radius: 10px; padding: 6px 12px;
    font-weight: 700; letter-spacing: 1px; min-width: 210px; }}
QComboBox:hover {{ border-color: {BLUE}; }}
QComboBox QAbstractItemView {{ background: {PANEL2}; selection-background-color: {BLUE}; border: 1px solid {BORDER}; }}
QSlider::groove:horizontal {{ height: 6px; background: {PANEL2}; border-radius: 3px; }}
QSlider::sub-page:horizontal {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {BLUE}, stop:1 {CYAN});
    border-radius: 3px; }}
QSlider::handle:horizontal {{ background: white; width: 16px; margin: -6px 0; border-radius: 8px; }}
QLabel#help {{ color: {MUTED}; font-size: 10px; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border: 1px solid {SKY}; border-radius: 4px; background: {PANEL2}; }}
QCheckBox::indicator:checked {{ background: {AMBER}; border-color: {AMBER}; }}
"""


def apply_dark(app):
    app.setStyle("Fusion")
    pal = app.palette()
    for role, col in ((pal.ColorRole.Window, BG), (pal.ColorRole.WindowText, TEXT),
                      (pal.ColorRole.Base, "#040814"), (pal.ColorRole.Text, TEXT),
                      (pal.ColorRole.Button, PANEL2), (pal.ColorRole.ButtonText, TEXT)):
        pal.setColor(role, QColor(col))
    app.setPalette(pal)
    app.setStyleSheet(QSS)


def mono(size, bold=False):
    f = QFont("JetBrains Mono")
    f.setStyleHint(QFont.Monospace)
    f.setPixelSize(size)
    f.setBold(bold)
    return f


class VideoPanel(QWidget):
    def __init__(self):
        super().__init__()
        self.img = None
        self.signal = False
        self.msg = "NO VIDEO"
        self.hud = ""
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumSize(480, 270)

    def set_image(self, img: QImage):
        self.img = img
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        clip = QPainterPath()
        clip.addRoundedRect(r, 14, 14)
        p.setClipPath(clip)
        g = QLinearGradient(0, 0, 0, self.height())
        g.setColorAt(0, QColor("#08122B"))
        g.setColorAt(1, QColor("#030712"))
        p.fillRect(self.rect(), QBrush(g))
        if self.img is not None and self.signal:
            s = self.img.size().scaled(self.size(), Qt.KeepAspectRatio)
            x, y = (self.width() - s.width()) // 2, (self.height() - s.height()) // 2
            p.drawImage(QRectF(x, y, s.width(), s.height()), self.img)
        else:
            p.setPen(QPen(QColor(59, 130, 246, 28), 1))
            for gx in range(0, self.width(), 40):
                p.drawLine(gx, 0, gx, self.height())
            for gy in range(0, self.height(), 40):
                p.drawLine(0, gy, self.width(), gy)
            p.setPen(QColor(SKY))
            f = QFont("Inter")
            f.setPixelSize(20)
            f.setBold(True)
            f.setLetterSpacing(QFont.AbsoluteSpacing, 6)
            p.setFont(f)
            p.drawText(self.rect(), Qt.AlignCenter, self.msg)
        # HUD: corner brackets, centre reticle, status line
        p.setClipping(False)
        p.setPen(QPen(QColor(SKY), 2))
        m, L = 16, 26
        w, h = self.width(), self.height()
        for (cx, cy, dx, dy) in ((m, m, 1, 1), (w - m, m, -1, 1), (m, h - m, 1, -1), (w - m, h - m, -1, -1)):
            p.drawLine(cx, cy, cx + dx * L, cy)
            p.drawLine(cx, cy, cx, cy + dy * L)
        if self.signal:
            p.setPen(QPen(QColor(34, 211, 238, 150), 1))
            c = QPointF(w / 2, h / 2)
            p.drawEllipse(c, 18, 18)
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                p.drawLine(c + QPointF(dx * 18, dy * 18), c + QPointF(dx * 30, dy * 30))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(GREEN if self.signal else RED))
        p.drawEllipse(QPointF(m + 12, m + 20), 4, 4)
        p.setPen(QColor(TEXT))
        p.setFont(mono(11, True))
        p.drawText(QPointF(m + 24, m + 24), ("LIVE   " if self.signal else "OFFLINE   ") + self.hud)
        p.setPen(QPen(QColor(BORDER), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(r, 14, 14)


class ThrusterBars(QWidget):
    """Thin white tick = commanded value; thick gradient bar = PWM reported by the Blackpill (mapped to -1000..1000)."""
    ROW = 30

    def __init__(self, names):
        super().__init__()
        self.names = names
        self.cmd = [0] * len(names)
        self.pwm = [None] * len(names)
        self.setFixedHeight(self.ROW * len(names) + 6)

    def set_values(self, cmd, pwm):
        self.cmd, self.pwm = cmd, pwm
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        lw, rw = 32, 96
        bw = self.width() - lw - rw
        cx = lw + bw / 2
        p.setFont(mono(11))
        for i, name in enumerate(self.names):
            y = 3 + i * self.ROW
            p.setPen(QColor(SKY))
            p.drawText(QRectF(0, y, lw, 22), Qt.AlignVCenter, name)
            track = QRectF(lw, y + 4, bw, 14)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(PANEL2))
            p.drawRoundedRect(track, 7, 7)
            pwm = self.pwm[i] if i < len(self.pwm) else None
            if pwm is not None and pwm != 1500:
                v = max(-1, min(1, (pwm - 1500) / 400))
                x0, x1 = sorted((cx, cx + bw / 2 * v))
                bar = QRectF(x0, y + 4, max(x1 - x0, 3), 14)
                gr = QLinearGradient(bar.left(), 0, bar.right(), 0)
                gr.setColorAt(0, QColor(BLUE))
                gr.setColorAt(1, QColor(CYAN))
                p.setBrush(gr)
                p.drawRoundedRect(bar, 7, 7)
            c = self.cmd[i] if i < len(self.cmd) else 0
            tx = cx + bw / 2 * max(-1, min(1, c / 1000))
            p.setBrush(QColor(255, 255, 255, 230))
            p.drawRoundedRect(QRectF(tx - 1.5, y + 1, 3, 20), 1.5, 1.5)
            p.setPen(QPen(QColor(MUTED), 1))
            p.drawLine(int(cx), y + 2, int(cx), y + 20)
            p.setPen(QColor(TEXT if pwm not in (None, 1500) else MUTED))
            p.drawText(QRectF(lw + bw + 8, y, rw, 22), Qt.AlignVCenter,
                       f"{c:+5d} {pwm if pwm is not None else '----'}")



class MainWindow(QMainWindow):
    def __init__(self, c, sim):
        super().__init__()
        self.c = c
        self.sim = sim
        net = c["network"]
        th = c["thrusters"]
        self.n = int(th["count"])
        self.names = th.get("names") or [f"T{i + 1}" for i in range(self.n)]
        self.link_ok_s = c["safety"]["link_ok_ms"] / 1000
        self.links = {"sim": c["sim"]["pi_ip"]} if sim else {"tether": net["pi_ip"], "wifi": net["wifi_pi_ip"]}
        dl = net.get("default_link", "tether")
        self.link_name = "sim" if sim else (dl if dl in self.links else "tether")
        pi_ip = self.links[self.link_name]
        self.lights_levels = sorted(int(x) for x in c["lights"]["levels"])
        self.lights_pct = 0
        self.lights_last = 50
        self.test_max = int(th.get("test_max_percent", 30))
        self.test_idx = None                      # thruster under bench test (None = normal piloting)
        self.test_val = 0

        self.mixer = Mixer(th["mixing"], th.get("reverse"))
        self.out = [int(o) - 1 for o in th.get("outputs") or range(1, self.n + 1)]
        if sorted(self.out) != list(range(self.n)):
            raise SystemExit(f"config: thrusters.outputs must use each output 1..{self.n} exactly once")
        if len(self.mixer.m) != self.n:
            raise SystemExit("config: thrusters.mixing must have thrusters.count rows")
        self.gains = list(c["gamepad"]["gains"])
        self.gain = int(c["gamepad"]["default_gain"])
        self.pad = Gamepad(c["gamepad"])
        self.link = ControlLink(pi_ip, net["control_port"], net["telemetry_port"], self.n,
                                c["rates"]["thrusters_hz"], c["rates"]["heartbeat_hz"]).start()
        self.video = VideoReceiver(net["video_port"], c["video"]["stall_timeout_s"], self)
        self.job = None
        self.confirmed_safe = sim
        self.was_ok = False

        apply_dark(QApplication.instance())
        self.setWindowTitle("ROV Control" + ("  [SIM]" if sim else f"  —  Pi {pi_ip}"))
        self._build_ui()
        self._update_titles()
        self._set_lights(int(c["lights"]["default"]))
        self.video.frame.connect(self._on_frame)
        self.video.log.connect(self.log)
        self.video.start()

        self.t_pad = QTimer(self)
        self.t_pad.timeout.connect(self._pad_tick)
        self.t_pad.start(20)                      # 50 Hz
        self.t_ui = QTimer(self)
        self.t_ui.timeout.connect(self._ui_tick)
        self.t_ui.start(50)                       # 20 Hz

        self.log(f"{'SIM mode' if sim else 'LIVE'}: control -> {pi_ip}:{net['control_port']}, "
                 f"telemetry :{net['telemetry_port']}, video :{net['video_port']}")
        if self.pad.error:
            self.log(self.pad.error)
        self.log("Keys: W/S surge, A/D sway, R/F heave, Q/E yaw, [ ] gain, SPACE = DISARM")

    # ---------- UI ----------
    @staticmethod
    def _card(title=None):
        f = QFrame()
        f.setObjectName("card")
        v = QVBoxLayout(f)
        v.setContentsMargins(14, 12, 14, 14)
        v.setSpacing(8)
        if title:
            h = QLabel(title.upper())
            h.setObjectName("h")
            v.addWidget(h)
        return f, v

    def _build_ui(self):
        root = QWidget()
        root.setObjectName("root")
        outer = QVBoxLayout(root)
        outer.setContentsMargins(16, 12, 16, 16)
        outer.setSpacing(12)

        # header
        head = QHBoxLayout()
        head.setSpacing(12)
        tbox = QVBoxLayout()
        tbox.setSpacing(0)
        t = QLabel("ROV  CONTROL")
        t.setObjectName("title")
        self.lbl_sub = QLabel()
        self.lbl_sub.setObjectName("sub")
        tbox.addWidget(t)
        tbox.addWidget(self.lbl_sub)
        head.addLayout(tbox)
        head.addStretch(1)
        self.cmb_link = QComboBox()
        self.cmb_link.setCursor(Qt.PointingHandCursor)
        self._fill_links()
        self.cmb_link.activated.connect(self._link_chosen)
        head.addWidget(self.cmb_link)
        self.lbl_link = QLabel()
        self.lbl_link.setAlignment(Qt.AlignCenter)
        self.lbl_link.setMinimumWidth(190)
        head.addWidget(self.lbl_link)
        outer.addLayout(head)

        body = QHBoxLayout()
        body.setSpacing(14)
        self.vpanel = VideoPanel()
        body.addWidget(self.vpanel, 1)

        side = QWidget()
        side.setFixedWidth(400)
        v = QVBoxLayout(side)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)

        # arm card
        c, cv = self._card("Propulsion")
        self.btn_arm = QPushButton("ARM")
        self.btn_arm.setMinimumHeight(60)
        self.btn_arm.setCursor(Qt.PointingHandCursor)
        self.btn_arm.clicked.connect(self._arm_clicked)
        cv.addWidget(self.btn_arm)
        self.lbl_state = QLabel()
        self.lbl_state.setAlignment(Qt.AlignCenter)
        cv.addWidget(self.lbl_state)
        hl = QLabel("PAD: Options ARM · Share/○ DISARM · L1/R1 gain · D-pad/△ lights\n"
                    "KEYS: WASD RF QE · SPACE disarm · [ ] gain · L - = lights")
        hl.setObjectName("help")
        hl.setWordWrap(True)
        cv.addWidget(hl)
        v.addWidget(c)

        # lights card (one PWM drives all 4 lights)
        c, cv = self._card("Lights")
        row = QHBoxLayout()
        self.sl_lights = QSlider(Qt.Horizontal)
        self.sl_lights.setRange(0, 100)
        self.sl_lights.valueChanged.connect(self._set_lights)
        row.addWidget(self.sl_lights, 1)
        self.lbl_lights = QLabel("OFF")
        self.lbl_lights.setObjectName("tileval")
        self.lbl_lights.setMinimumWidth(120)
        self.lbl_lights.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row.addWidget(self.lbl_lights)
        cv.addLayout(row)
        brow = QHBoxLayout()
        for pct in (0, 25, 50, 100):
            b = QPushButton("OFF" if pct == 0 else f"{pct}%")
            b.setObjectName("chip")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, p=pct: self._set_lights(p))
            brow.addWidget(b)
        cv.addLayout(brow)
        v.addWidget(c)

        # thrusters card
        c, cv = self._card("Thrusters")
        self.bars = ThrusterBars(self.names)
        cv.addWidget(self.bars)
        lrow = QHBoxLayout()
        leg = QLabel("tick = command  ·  bar = PWM from Blackpill")
        leg.setObjectName("sub")
        lrow.addWidget(leg, 1)
        b = QPushButton("SETUP")
        b.setObjectName("chip")
        b.setCursor(Qt.PointingHandCursor)
        b.setToolTip("Which output/pin each thruster is on, and reverse")
        b.clicked.connect(self._thruster_setup)
        lrow.addWidget(b)
        cv.addLayout(lrow)
        trow = QHBoxLayout()
        tl = QLabel("BENCH TEST")
        tl.setObjectName("tilekey")
        trow.addWidget(tl)
        self.btn_test = []
        for i, name in enumerate(self.names):
            b = QPushButton(name)
            b.setObjectName("chip")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(f"Spin only {name} with the slider below (sticks ignored). Props off / clear!")
            b.clicked.connect(lambda checked, i=i: self._select_test(i, checked))
            trow.addWidget(b)
            self.btn_test.append(b)
        cv.addLayout(trow)
        self.sl_test = QSlider(Qt.Horizontal)
        self.sl_test.setRange(-self.test_max, self.test_max)
        self.sl_test.setEnabled(False)
        self.sl_test.setToolTip(f"Hold to spin (max {self.test_max}%). Springs back to 0 when released.")
        self.sl_test.valueChanged.connect(lambda val: setattr(self, "test_val", val))
        self.sl_test.sliderReleased.connect(lambda: self.sl_test.setValue(0))
        cv.addWidget(self.sl_test)
        v.addWidget(c)

        # telemetry tiles
        c, cv = self._card("Telemetry")
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        self.tiles = {}
        for i, key in enumerate(("RX OK", "RX BAD", "BP UPTIME", "VIDEO", "TX", "INPUT")):
            tile = QVBoxLayout()
            tile.setSpacing(0)
            k = QLabel(key)
            k.setObjectName("tilekey")
            val = QLabel("-")
            val.setObjectName("tileval")
            tile.addWidget(k)
            tile.addWidget(val)
            grid.addLayout(tile, i // 3, i % 3)
            self.tiles[key] = val
        cv.addLayout(grid)
        v.addWidget(c)

        # actions
        g = QGridLayout()
        g.setSpacing(8)
        self.btn_reset = QPushButton("Reset Blackpill")
        self.btn_flash = QPushButton("Flash firmware…")
        self.btn_cam = QPushButton("Restart camera")
        for b in (self.btn_reset, self.btn_flash, self.btn_cam):
            b.setObjectName("action")
            b.setCursor(Qt.PointingHandCursor)
        self.btn_reset.clicked.connect(self._reset)
        self.btn_flash.clicked.connect(self._flash)
        self.btn_cam.clicked.connect(self._restart_cam)
        g.addWidget(self.btn_reset, 0, 0)
        g.addWidget(self.btn_flash, 0, 1)
        g.addWidget(self.btn_cam, 1, 0, 1, 2)
        v.addLayout(g)

        self.logbox = QPlainTextEdit()
        self.logbox.setMinimumHeight(60)
        self.logbox.setReadOnly(True)
        self.logbox.setMaximumBlockCount(2000)
        v.addWidget(self.logbox, 1)
        body.addWidget(side)
        outer.addLayout(body, 1)
        self.setCentralWidget(root)
        self.resize(1440, 860)
        # Nothing may take keyboard focus: SPACE must always mean DISARM, never "press focused button"
        for w in self.findChildren(QWidget):
            w.setFocusPolicy(Qt.NoFocus)

    def log(self, msg):
        line = time.strftime("%H:%M:%S ") + msg
        self.logbox.appendPlainText(line)
        print(line, flush=True)

    # ---------- thruster -> board output (thrusters.outputs) ----------
    def _to_wire(self, vals):
        w = [0] * self.n
        for i, v in enumerate(vals):
            w[self.out[i]] = v
        return w

    def _from_wire(self, w):
        return [w[o] for o in self.out]

    # ---------- link / lights / bench test ----------
    def _ssh(self):
        return f"{self.c['pi']['user']}@{self.links[self.link_name]}"

    def _update_titles(self):
        ip = self.links[self.link_name]
        self.setWindowTitle(f"ROV Control  —  {self.link_name.upper()}  {ip}")
        self.lbl_sub.setText(("SIMULATION" if self.sim else "LIVE") + f"  ·  {self.link_name.upper()} {ip}"
                             f"  ·  {self.n} THRUSTERS")

    def _fill_links(self):
        self.cmb_link.clear()
        for name, ip in self.links.items():
            self.cmb_link.addItem(f"{name.upper()}  ·  {ip}", name)
        if not self.sim:
            self.cmb_link.addItem("Set Wi-Fi Pi IP…", "_edit")
        self.cmb_link.setCurrentIndex(list(self.links).index(self.link_name))

    def _link_chosen(self, i):
        name = self.cmb_link.itemData(i)
        if name == "_edit":
            ip, ok = QInputDialog.getText(self, "Wi-Fi link", "Pi Wi-Fi IP (run 'hostname -I' on the Pi):",
                                          text=self.links["wifi"])
            if not (ok and ip.strip()):
                self._fill_links()
                return
            self.links["wifi"] = ip.strip()
            cfgmod.save_local("network.wifi_pi_ip", self.links["wifi"])
            name = "wifi"
        if name != self.link_name or name == "wifi":
            if self.link.board_armed():
                self.disarm("switching link")
            self.link_name = name
            self.link.set_dest(self.links[name])
            if not self.sim:
                cfgmod.save_local("network.default_link", name)
        self._fill_links()
        self._update_titles()

    def _set_lights(self, pct):
        pct = max(0, min(100, int(pct)))
        self.lights_pct = pct
        if pct:
            self.lights_last = pct
        self.link.set_lights(pct * 10)
        self.sl_lights.blockSignals(True)
        self.sl_lights.setValue(pct)
        self.sl_lights.blockSignals(False)

    def _step_lights(self, d):
        lv = self.lights_levels
        nxt = [x for x in lv if x > self.lights_pct] if d > 0 else [x for x in lv if x < self.lights_pct]
        if nxt:
            self._set_lights(nxt[0] if d > 0 else nxt[-1])

    def _toggle_lights(self):
        self._set_lights(0 if self.lights_pct else self.lights_last)

    def _select_test(self, i, checked):
        for j, b in enumerate(self.btn_test):
            if j != i:
                b.setChecked(False)
        self.sl_test.setValue(0)
        self.test_idx = i if checked else None
        self.sl_test.setEnabled(checked)
        if checked:
            self.log(f"BENCH TEST {self.names[i]}: arm, then hold the slider (max {self.test_max}%). Sticks ignored.")
        else:
            self.log("bench test off: sticks control the thrusters again")

    # ---------- arming ----------
    def _arm_clicked(self):
        if self.link.board_armed():
            self.disarm("button")
        else:
            self.arm("button")

    def _thruster_setup(self):
        if self.link.board_armed():
            QMessageBox.information(self, "Thruster setup", "Disarm first.")
            return
        self.setup_open = True
        try:
            d = ThrusterSetup(self.names, self.out, self.mixer.reverse, self)
            if d.exec() != QDialog.Accepted:
                return
            out, rev = d.result_values()
        finally:
            self.setup_open = False
        self.out, self.mixer.reverse = out, rev
        cfgmod.save_local("thrusters.outputs", [o + 1 for o in out], cfgmod.ROV_PATH)
        cfgmod.save_local("thrusters.reverse", rev, cfgmod.ROV_PATH)
        self.log("thruster setup saved: " + "  ".join(
            f"{n}->{o + 1}{'(rev)' if r else ''}" for n, o, r in zip(self.names, out, rev)))

    def arm(self, src):
        if getattr(self, "setup_open", False):
            self.log("ARM refused: close Thruster setup first")
            return
        if any(abs(x) > 0.05 for x in self._last_cmd.values()):
            self.log("ARM refused: center the sticks / release keys first")
            return
        if not self.confirmed_safe:
            r = QMessageBox.warning(self, "Arm thrusters",
                                    "Arm the REAL thrusters?\n\nProps clear of people, tether and hands?",
                                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if r != QMessageBox.Yes:
                self.log("ARM cancelled")
                return
            self.confirmed_safe = True
        self.log(f"ARM ({src})")
        self.link.request_arm(True)

    def disarm(self, why):
        self.log(f"DISARM ({why})")
        self.link.request_arm(False)

    # ---------- periodic ----------
    _last_cmd = dict.fromkeys(("surge", "sway", "heave", "roll", "pitch", "yaw"), 0.0)

    def _pad_tick(self):
        cmd, events = self.pad.poll()
        self._last_cmd = cmd
        for ev in events:
            if ev == "arm":
                self.arm("gamepad Start")
            elif ev == "disarm":
                self.disarm("gamepad Back")
            elif ev in ("gain_up", "gain_down"):
                self._step_gain(1 if ev == "gain_up" else -1)
            elif ev == "lights_up":
                self._step_lights(1)
            elif ev == "lights_down":
                self._step_lights(-1)
            elif ev == "lights_toggle":
                self._toggle_lights()
            elif ev.startswith("connected:"):
                self.log(f"gamepad connected: {ev.split(':', 1)[1]}")
            elif ev == "disconnected":
                self.log("gamepad DISCONNECTED")
                self.disarm("gamepad lost")
        if self.test_idx is not None:
            vals = [0] * self.n
            v = self.test_val * 10                              # percent -> -1000..1000
            vals[self.test_idx] = -v if self.mixer.reverse[self.test_idx] else v
            self.link.set_thrusters(self._to_wire(vals))
        else:
            self.link.set_thrusters(self._to_wire(self.mixer.mix(cmd, self.gain / 100.0)))

    def _step_gain(self, d):
        i = self.gains.index(self.gain) if self.gain in self.gains else 0
        self.gain = self.gains[max(0, min(len(self.gains) - 1, i + d))]
        self.log(f"gain {self.gain}%")

    def _ui_tick(self):
        while self.link.logq:
            self.log(self.link.logq.popleft())
        t, age, cmd = self.link.snapshot()
        cmd = self._from_wire(cmd)
        ok = age is not None and age < self.link_ok_s
        if ok:
            self._pill(self.lbl_link, f"LINK OK  ({age * 1000:.0f} ms)", GREEN)
        else:
            self._pill(self.lbl_link, "NO TELEMETRY" if age is None else f"LINK LOST ({age:.1f} s)", RED)
        armed = bool(t and t.armed and ok)
        if self.was_ok and not ok:
            self.log("telemetry lost (Pi bridge / Blackpill not responding)")
        elif ok and not self.was_ok:
            self.log("telemetry OK")
        self.was_ok = ok
        self.btn_arm.setText("DISARM" if armed else "ARM")
        if armed:
            grad, edge = "stop:0 #F87171, stop:1 #B91C1C", "#FCA5A5"
        else:
            grad, edge = f"stop:0 {SKY}, stop:1 #1D4ED8", "#93C5FD"
        self.btn_arm.setStyleSheet(
            f"QPushButton {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1,{grad}); color: white;"
            f" border: 1px solid {edge}; border-radius: 12px; font-size: 22px; font-weight: 800; letter-spacing: 6px; }}"
            f"QPushButton:hover {{ border: 2px solid white; }}")
        if t is None:
            self._pill(self.lbl_state, "Blackpill: unknown", GREY)
        elif t.failsafe:
            self._pill(self.lbl_state, "FAILSAFE — link was lost, press ARM", AMBER)
        else:
            self._pill(self.lbl_state, f"Blackpill {'ARMED' if t.armed else 'disarmed'}   gain {self.gain}%",
                       RED if t.armed else GREY)
        pwm = self._from_wire(t.pwm_us) if (t and ok) else [None] * self.n
        self.bars.set_values(cmd if armed else [0] * self.n, pwm)
        vs = "no GStreamer" if not self.video.available else f"{self.video.fps:4.1f} fps"
        pad = self.pad.name or "keyboard"
        up = f"{t.uptime_ms / 1000:.0f}s" if t else "-"
        tl = self.tiles
        tl["RX OK"].setText(str(t.rx_ok) if t else "-")
        tl["RX BAD"].setText(str(t.rx_bad) if t else "-")
        tl["BP UPTIME"].setText(up)
        tl["VIDEO"].setText(vs.strip())
        tl["TX"].setText(str(self.link.tx_count))
        tl["INPUT"].setText(pad[:12])
        tl["RX BAD"].setStyleSheet(f"color: {AMBER};" if t and t.rx_bad else "")
        self.vpanel.signal = bool(self.video.has_signal())
        self.vpanel.msg = "NO VIDEO" if self.video.available else "NO VIDEO (GStreamer missing)"
        self.lbl_lights.setText(("OFF" if not self.lights_pct else f"{self.lights_pct}%")
                                + (f"  {t.lights_us}µs" if t and ok else ""))
        img = self.vpanel.img
        self.vpanel.hud = (f"{img.width()}x{img.height()}  {self.video.fps:.0f} fps" if img is not None else "")
        self.vpanel.update()

    @staticmethod
    def _pill(lbl, text, color):
        lbl.setText(text)
        lbl.setStyleSheet(f"background: {color}22; color: {color}; border: 1px solid {color}; font-weight: 700;"
                          f" letter-spacing: 1px; font-size: 11px; padding: 7px 12px; border-radius: 10px;")

    def _on_frame(self, img):
        self.vpanel.set_image(img)

    # ---------- remote actions ----------
    def _start_job(self, job):
        if self.job and self.job.isRunning():
            self.log("another remote action is still running")
            return
        self.job = job
        job.line.connect(self.log)
        job.done.connect(lambda ok, title: self.log(f"{title}: {'OK' if ok else 'FAILED'}"))
        job.start()

    def _reset(self):
        if self.link.board_armed():
            self.disarm("before reset")
        self._start_job(remote.sim_job("Reset Blackpill", self) if self.sim else remote.reset_job(self._ssh(), self))

    def _flash(self):
        if self.link.board_armed() or (self.link.snapshot()[0] and self.link.snapshot()[0].armed):
            self.log("FLASH REFUSED: disarm first")
            QMessageBox.warning(self, "Flash", "Refusing to flash while ARMED. Disarm first.")
            return
        path, _ = QFileDialog.getOpenFileName(self, "Blackpill firmware", os.path.expanduser("~"),
                                              "Firmware (*.bin);;All files (*)")
        if not path:
            return
        f = self.c["flash"]
        self._start_job(remote.sim_job(f"Flash {os.path.basename(path)}", self) if self.sim else
                        remote.flash_job(self._ssh(), path, f["remote_tmp"], f["address"], self))

    def _restart_cam(self):
        self._start_job(remote.sim_job("Restart camera", self) if self.sim else
                        remote.restart_camera_job(self._ssh(), self))

    # ---------- keys / close ----------
    def handle_key(self, ev, down):
        if ev.isAutoRepeat():
            return True
        k = ev.key()
        if k == Qt.Key_Space and down:
            self.disarm("SPACE")
            return True
        if down and k == Qt.Key_BracketRight:
            self._step_gain(1)
            return True
        if down and k == Qt.Key_BracketLeft:
            self._step_gain(-1)
            return True
        if down and k == Qt.Key_L:
            self._toggle_lights()
            return True
        if down and k == Qt.Key_Minus:
            self._step_lights(-1)
            return True
        if down and k == Qt.Key_Equal:
            self._step_lights(1)
            return True
        name = ev.text().upper()
        if name in ("W", "A", "S", "D", "Q", "E", "R", "F"):
            (self.pad.keys.add if down else self.pad.keys.discard)(name)
            return True
        return False

    def changeEvent(self, e):
        if e.type() == QEvent.ActivationChange and not self.isActiveWindow():
            self.pad.keys.clear()          # don't keep "holding" keys when focus leaves
        super().changeEvent(e)

    def closeEvent(self, e):
        self.log("closing: DISARM")
        self.t_pad.stop()
        self.link.stop(disarm=True)
        self.video.stop()
        e.accept()


class KeyFilter(QObject):
    def __init__(self, win):
        super().__init__()
        self.win = win

    def eventFilter(self, obj, ev):
        t = ev.type()
        if t in (QEvent.KeyPress, QEvent.KeyRelease) and isinstance(obj, QWidget) and obj.window() is self.win:
            if self.win.handle_key(ev, t == QEvent.KeyPress):
                return True
        return False


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sim", action="store_true", help="talk to sim/run_sim.sh on 127.0.0.1")
    ap.add_argument("--config", help="alternate system.yaml")
    a = ap.parse_args()
    c = cfgmod.load(a.config)
    app = QApplication(sys.argv)
    app.setApplicationName("ROV Control")
    win = MainWindow(c, a.sim)
    kf = KeyFilter(win)
    app.installEventFilter(kf)
    signal.signal(signal.SIGINT, lambda *_: win.close())    # Ctrl+C in terminal = clean disarm + exit
    sig_timer = QTimer()
    sig_timer.timeout.connect(lambda: None)                 # let Python see signals
    sig_timer.start(200)
    win.show()
    rc = app.exec()
    # closeEvent already disarmed and stopped video. Skip interpreter finalization:
    # PySide6 + GStreamer (gi) can double-free native objects there. (The 'deallocating None' aborts
    # came from PySide6 6.12.0 on Python 3.10; setup_laptop.sh pins 6.8.3.)
    sys.stdout.flush(); sys.stderr.flush()
    os._exit(rc)


if __name__ == "__main__":
    main()
