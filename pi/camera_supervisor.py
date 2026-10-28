"""Keeps pi/camera.sh streaming to whichever laptop the control packets come from.

The laptop's address is learned, never configured: plug in the FXTI tether or use Wi-Fi and the video follows.
The camera is restarted if it dies (the app's "Restart camera" button simply kills it).
"""
import logging
import os
import signal
import subprocess
import time

HERE = os.path.dirname(os.path.abspath(__file__))
log = logging.getLogger("camera")
RESTART_S = 2.0


class Camera:
    def __init__(self, enabled=True, logfile="/tmp/rov_camera.log"):
        self.enabled = enabled
        self.logfile = logfile
        self.proc = None
        self.ip = None
        self.next_start = 0.0

    def follow(self, ip):
        if self.enabled and ip != self.ip:
            log.info("video -> %s", ip)
            self.ip = ip
            self.stop()
            self.next_start = 0.0

    def poll(self):
        """Call often (from the bridge loop)."""
        if not self.enabled or not self.ip:
            return
        if self.proc is not None and self.proc.poll() is None:
            return
        now = time.monotonic()
        if self.proc is not None:
            log.warning("camera exited (code %s), restarting", self.proc.returncode)
            self.proc = None
            self.next_start = now + RESTART_S
        if now >= self.next_start:
            env = dict(os.environ, ROV_LAPTOP_IP=self.ip)
            with open(self.logfile, "ab") as f:
                self.proc = subprocess.Popen(["bash", os.path.join(HERE, "camera.sh")], env=env, stdout=f,
                                             stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                             start_new_session=True)

    def stop(self):
        if self.proc is None or self.proc.poll() is not None:
            self.proc = None
            return
        try:
            os.killpg(self.proc.pid, signal.SIGINT)     # rpicam-vid ignores SIGTERM
            self.proc.wait(timeout=3)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        self.proc = None
