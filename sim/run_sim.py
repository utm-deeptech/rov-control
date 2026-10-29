#!/usr/bin/env python3
"""Whole system on this computer, no hardware (cross-platform; Windows uses this, Linux can use sim/run_sim.sh):

    sim_host (fake Pi + Blackpill) | test video -> :video_port (needs ffmpeg or GStreamer) | app --sim

The fake Blackpill's live status stays in THIS terminal. Closing the app or Ctrl+C stops everything.
"""
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for sub in ("config", "sim"):
    sys.path.insert(0, os.path.join(ROOT, sub))
import cfg as cfgmod  # noqa: E402
import sim_host  # noqa: E402


def test_video_cmd(c):
    v, host, port = c["video"], c["sim"]["laptop_ip"], c["network"]["video_port"]
    size, fps, kbps, gop = f"{v['width']}x{v['height']}", v["fps"], v["bitrate"] // 1000, v["intra"]
    if shutil.which("ffmpeg"):
        return ["ffmpeg", "-loglevel", "error", "-re", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate={fps}",
                "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency", "-pix_fmt", "yuv420p",
                "-b:v", f"{kbps}k", "-g", str(gop), "-x264-params", "repeat-headers=1",
                "-f", "rtp", "-payload_type", "96", f"rtp://{host}:{port}"]
    if shutil.which("gst-launch-1.0"):
        return ["gst-launch-1.0", "-q", "videotestsrc", "is-live=true", "pattern=ball", "!",
                f"video/x-raw,width={v['width']},height={v['height']},framerate={fps}/1", "!", "videoconvert", "!",
                "x264enc", "tune=zerolatency", "speed-preset=ultrafast", f"bitrate={kbps}", f"key-int-max={gop}",
                "!", "h264parse", "!", "rtph264pay", "config-interval=1", "pt=96", "!", "udpsink",
                f"host={host}", f"port={port}", "sync=false"]
    return None


def main():
    if os.name == "nt":
        os.system("")                               # enable ANSI colours in the console
    c = cfgmod.load()
    logs = os.path.join(ROOT, "sim", "logs")
    os.makedirs(logs, exist_ok=True)
    procs = []

    vcmd = test_video_cmd(c)
    if vcmd:
        procs.append(subprocess.Popen(vcmd, stdout=subprocess.DEVNULL, stderr=open(os.path.join(logs, "video.log"), "w")))
        print(f"[sim] test video -> {c['sim']['laptop_ip']}:{c['network']['video_port']} ({os.path.basename(vcmd[0])})")
    else:
        print("[sim] no ffmpeg or GStreamer found: running without test video (install ffmpeg to get it)")

    app = subprocess.Popen([sys.executable, os.path.join(ROOT, "topside", "app.py"), "--sim"],
                           stdout=open(os.path.join(logs, "app.log"), "w"), stderr=subprocess.STDOUT)
    procs.append(app)
    print("[sim] app started (log: sim/logs/app.log). Closing the app or Ctrl+C here ends the sim.\n")
    try:
        sim_host.run(c["network"]["control_port"], c["network"]["telemetry_port"], c["sim"]["laptop_ip"],
                     c["thrusters"]["count"], c["safety"]["failsafe_ms"], c["rates"]["telemetry_hz"],
                     stop=lambda: app.poll() is not None)
        time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        print("\n[sim] stopping...")
        for p in procs:
            if p.poll() is None:
                p.terminate()
        for p in procs:
            try:
                p.wait(timeout=3)
            except subprocess.TimeoutExpired:
                p.kill()


if __name__ == "__main__":
    main()
