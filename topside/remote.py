"""Remote actions on the Pi over SSH (reset/flash Blackpill, restart camera), in a QThread.

Each action is a list of attempts; the next attempt runs only if the previous one failed.
"""
import shlex
import subprocess

from PySide6.QtCore import QThread, Signal


class RemoteJob(QThread):
    line = Signal(str)
    done = Signal(bool, str)          # ok, title

    def __init__(self, title, attempts, parent=None):
        super().__init__(parent)
        self.title = title
        self.attempts = attempts       # list[list[list[str]]]: attempts -> steps -> argv

    def run(self):
        for i, steps in enumerate(self.attempts):
            if i:
                self.line.emit("retrying with --connect-under-reset ...")
            ok = all(self._run(argv) for argv in steps)
            if ok:
                self.done.emit(True, self.title)
                return
        self.done.emit(False, self.title)

    def _run(self, argv):
        self.line.emit("$ " + " ".join(shlex.quote(a) for a in argv))
        try:
            p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                 stdin=subprocess.DEVNULL)
        except FileNotFoundError as e:
            self.line.emit(str(e))
            return False
        for ln in p.stdout:
            self.line.emit(ln.rstrip())
        rc = p.wait()
        if rc:
            self.line.emit(f"(exit {rc})")
        return rc == 0


SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5"]


def reset_job(pi, parent=None):
    return RemoteJob("Reset Blackpill", [
        [SSH + [pi, "st-flash reset"]],
        [SSH + [pi, "st-flash --connect-under-reset reset"]],
    ], parent)


def flash_job(pi, local_bin, remote_tmp, address, parent=None):
    copy = ["scp", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", local_bin, f"{pi}:{remote_tmp}"]
    return RemoteJob("Flash firmware", [
        [copy, SSH + [pi, f"st-flash --reset write {remote_tmp} {address}"]],
        [copy, SSH + [pi, f"st-flash --connect-under-reset --reset write {remote_tmp} {address}"]],
    ], parent)


def restart_camera_job(pi, parent=None):
    # the bridge restarts the camera within ~2 s of it exiting
    return RemoteJob("Restart camera", [[SSH + [pi, "pkill -INT -x rpicam-vid || pkill -INT -x libcamera-vid || true"]]],
                     parent)


def sim_job(title, parent=None):
    return RemoteJob(title + " (sim: no hardware)", [[["echo", f"[sim] would run: {title}"]]], parent)
