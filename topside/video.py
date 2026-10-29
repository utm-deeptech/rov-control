"""H.264 RTP/UDP receiver -> QImage. Auto-recovers when the stream stops.

Two backends, same behaviour: GStreamer appsink (Linux: apt python3-gi) and PyAV (Windows/macOS: pip install av,
no system packages). GStreamer is used when its Python bindings import; otherwise PyAV.
"""
import os
import tempfile
import threading
import time

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QImage

try:
    import gi
    gi.require_version("Gst", "1.0")
    from gi.repository import Gst
    Gst.init(None)
    GST_ERROR = None
except Exception as e:  # pragma: no cover
    Gst = None
    GST_ERROR = str(e)

try:
    import av
    AV_ERROR = None
except Exception as e:  # pragma: no cover
    av = None
    AV_ERROR = str(e)

# PyAV reads the RTP stream through an SDP description (the sender is rtph264pay, payload type 96).
SDP = (
    "v=0\r\no=- 0 0 IN IP4 127.0.0.1\r\ns=ROV\r\nc=IN IP4 0.0.0.0\r\nt=0 0\r\n"
    "m=video {port} RTP/AVP 96\r\na=rtpmap:96 H264/90000\r\n"
)
AV_OPTIONS = {"protocol_whitelist": "file,udp,rtp", "fflags": "nobuffer", "flags": "low_delay",
              "analyzeduration": "500000", "probesize": "65536", "buffer_size": "1048576"}

PIPELINE = (
    'udpsrc port={port} buffer-size=1048576 caps="application/x-rtp,media=video,encoding-name=H264,payload=96" ! '
    # max-threads=1: libav frame-threading buffers one frame per thread (~100+ ms on a laptop). 720p decodes fine on 1.
    "rtph264depay ! h264parse ! avdec_h264 max-threads=1 ! videoconvert ! video/x-raw,format=RGB ! "
    "appsink name=sink drop=true max-buffers=1 sync=false emit-signals=true"
)


class VideoReceiver(QObject):
    frame = Signal(QImage)
    log = Signal(str)

    def __init__(self, port, stall_timeout_s=2.0, parent=None):
        super().__init__(parent)
        self.port = port
        self.stall = stall_timeout_s
        self.pipe = None
        self.last_frame = 0.0
        self.started = 0.0
        self.frames = 0
        self.fps = 0.0
        self._fps_t = time.monotonic()
        self._fps_n = 0
        self.watchdog = QTimer(self)
        self.watchdog.timeout.connect(self._check)
        self._av_run = False
        self._av_thread = None

    @property
    def available(self):
        return Gst is not None or av is not None

    def start(self):
        if Gst is not None:
            self._build()
            self.watchdog.start(500)
        elif av is not None:
            self._av_start()
            self.watchdog.start(500)
        else:
            self.log.emit(f"no video backend (GStreamer: {GST_ERROR}; PyAV: {AV_ERROR}) — "
                          "run scripts/setup_laptop.sh (Linux) or rov setup (Windows)")

    def _build(self):
        self._teardown()
        self.pipe = Gst.parse_launch(PIPELINE.format(port=self.port))
        sink = self.pipe.get_by_name("sink")
        sink.connect("new-sample", self._on_sample)
        self.pipe.set_state(Gst.State.PLAYING)
        self.started = time.monotonic()

    def _teardown(self):
        if self.pipe is not None:
            self.pipe.set_state(Gst.State.NULL)
            self.pipe = None

    def stop(self):
        self.watchdog.stop()
        self._teardown()
        self._av_run = False          # the PyAV thread is a daemon and ends within its 1 s read timeout

    # GStreamer streaming thread
    def _on_sample(self, sink):
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.OK
        buf = sample.get_buffer()
        s = sample.get_caps().get_structure(0)
        w, h = s.get_value("width"), s.get_value("height")
        ok, info = buf.map(Gst.MapFlags.READ)
        if ok:
            try:
                stride = len(info.data) // h if h else w * 3
                img = QImage(info.data, w, h, stride, QImage.Format_RGB888).copy()
            finally:
                buf.unmap(info)
            self.last_frame = time.monotonic()
            self._fps_n += 1
            self.frame.emit(img)       # queued to the GUI thread
        return Gst.FlowReturn.OK

    # ---- PyAV backend: one thread that (re)opens the stream, decodes, and emits frames ----
    def _av_start(self):
        self.started = time.monotonic()
        self._av_run = True
        self._av_thread = threading.Thread(target=self._av_loop, name="video", daemon=True)
        self._av_thread.start()

    def _av_loop(self):
        fd, sdp_path = tempfile.mkstemp(suffix=".sdp", prefix="rov_")
        with os.fdopen(fd, "w") as f:
            f.write(SDP.format(port=self.port))
        try:
            while self._av_run:
                try:
                    self._av_receive(sdp_path)
                except Exception as e:      # stream not there yet / ended / decode error: just retry
                    if self._av_run and self.last_frame:
                        self.log.emit(f"video: {e} — restarting receiver")
                    self.last_frame = 0.0
                time.sleep(0.2)
        finally:
            try:
                os.remove(sdp_path)
            except OSError:
                pass

    def _av_receive(self, sdp_path):
        # (open, read) timeouts in s: open returns/raises if nothing arrives, so _av_run is re-checked
        with av.open(sdp_path, format="sdp", options=AV_OPTIONS, timeout=(1.0, 1.0)) as c:
            stream = c.streams.video[0]
            stream.thread_type = "SLICE"      # frame threading would buffer one frame per thread (latency)
            stream.codec_context.thread_count = 1
            for frame in c.decode(stream):
                if not self._av_run:
                    return
                rgb = frame.reformat(format="rgb24")
                plane = rgb.planes[0]
                img = QImage(bytes(plane), rgb.width, rgb.height, plane.line_size, QImage.Format_RGB888).copy()
                self.last_frame = time.monotonic()
                self._fps_n += 1
                self.frame.emit(img)

    # GUI thread
    def _check(self):
        now = time.monotonic()
        if now - self._fps_t >= 1.0:
            self.fps = self._fps_n / (now - self._fps_t)
            self._fps_n = 0
            self._fps_t = now
        if Gst is None:
            return                    # PyAV thread restarts itself
        bus = self.pipe.get_bus() if self.pipe else None
        while bus:
            msg = bus.pop()           # drain everything (no GLib main loop here)
            if msg is None:
                break
            if not msg.type & (Gst.MessageType.ERROR | Gst.MessageType.EOS):
                continue
            if msg.type == Gst.MessageType.ERROR:
                err, _ = msg.parse_error()
                self.log.emit(f"video error: {err.message} — restarting receiver")
            self._build()
            return
        ref = max(self.last_frame, self.started)
        if now - ref > self.stall:
            if self.last_frame:
                self.log.emit("video stalled — restarting receiver")
            self.last_frame = 0.0
            self._build()

    def has_signal(self):
        return self.last_frame and time.monotonic() - self.last_frame < self.stall
