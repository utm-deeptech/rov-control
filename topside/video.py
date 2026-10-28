"""H.264 RTP/UDP receiver -> QImage, via GStreamer appsink. Auto-recovers when the stream stops."""
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

    @property
    def available(self):
        return Gst is not None

    def start(self):
        if Gst is None:
            self.log.emit(f"GStreamer not available ({GST_ERROR}) — run scripts/setup_laptop.sh")
            return
        self._build()
        self.watchdog.start(500)

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

    # GUI thread
    def _check(self):
        now = time.monotonic()
        if now - self._fps_t >= 1.0:
            self.fps = self._fps_n / (now - self._fps_t)
            self._fps_n = 0
            self._fps_t = now
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
