# ROV Control on Windows 10/11

Ubuntu 22.04+ is the primary, best-tested platform (see the main [README](../README.md)). Windows is supported with the
same app; this page is everything that differs.

What was checked on Windows (by the author, on a Windows 11 PC): all tests, the simulator (`run-sim`), the app starting
and connecting to a simulated board, video decoding of a test stream, and the packaged `.exe`. **Not yet checked on
Windows:** a real ROV, the FXTI tether, and a real RC radio. Do the README's "not yet verified" list there too.

## Install (pick one)

**A. Packaged app: nothing to install.** Get `ROV-Control-windows.zip` (a GitHub Release, or ask a teammate), unzip it
into a normal folder (Documents, not Program Files), run `ROV Control.exe`. Windows SmartScreen will warn that the
publisher is unknown (the app is not code-signed): **More info > Run anyway**. Settings the app saves (Wi-Fi IP, thruster
wiring, RC calibration) go in the `config\` folder next to the `.exe`; the log is `rov-control.log` there.

**B. One click from source:** double-click `Install.bat`. It installs Python through winget if missing, sets up the
packages and puts a "ROV Control" icon on the Desktop.

**C. By hand.** Needs Python 3.10-3.13 (`winget install Python.Python.3.12`, or python.org with "Add python.exe to PATH"
ticked). In PowerShell or Command Prompt, in the repo folder:

```bat
.\rov.cmd setup        :: .venv + pinned packages (PySide6, pygame, PyAV for video)
.\rov.cmd test         :: all green (the Linux-only pty tests and the C firmware tests are skipped)
.\rov.cmd run-sim      :: whole system on this PC, no hardware (test video needs ffmpeg: winget install Gyan.FFmpeg)
.\rov.cmd run          :: the real thing
.\rov.cmd shortcut     :: "ROV Control" icon on the Desktop
.\rov.cmd rc-setup     :: calibrate an RC radio (RadioMaster, ...)
.\rov.cmd update       :: get the latest version (a copy cloned with git)
.\rov.cmd build        :: make dist\ROV-Control-windows.zip (the packaged app)
```

## Tether (FXTI)

Plug in the FXTI, open PowerShell **as Administrator** and run `.\rov.cmd network`. It gives the FXTI adapter
`192.168.2.1/24` (no gateway, so Wi-Fi keeps the internet) and adds a Windows Firewall rule for the telemetry and video
UDP ports. Without that rule Windows treats the tether as a *Public* network and silently drops incoming UDP, so the
video never arrives. (If you skip it, click **Allow** when Windows asks about `python.exe` on first run, on *all*
network types.) Then choose **Link > TETHER** in the app.

## Differences from Ubuntu

* **Video** is decoded by PyAV (ffmpeg, installed by pip) instead of GStreamer. The Pi side is unchanged.
* **Controllers:** PS4, PS5 and Xbox pads and RC radios work as-is (USB or Bluetooth). A **PS3** pad needs a driver on
  Windows (DsHidMini or the SCP Driver Package); check what the app sees with `.\rov.cmd gamepad-test`.
* **Reset / Flash / Restart camera** buttons use the `ssh`/`scp` built into Windows (Settings > Apps > Optional features >
  OpenSSH Client). They run non-interactively, so set up a key once: `ssh-keygen`, then append
  `%USERPROFILE%\.ssh\id_ed25519.pub` to `~/.ssh/authorized_keys` on the Pi.
* **Building firmware** (`make fw`) and the C firmware unit test need `gcc-arm-none-eabi` / `gcc`: use WSL, an Ubuntu
  machine, or the prebuilt binaries in `firmware/prebuilt/` (flash them with the app's Flash button, or STM32CubeProgrammer
  over USB DFU).
* **Bench mode** with `nmcli` (README section 6) is for a Linux laptop; on Windows use `.\rov.cmd network`.
* **The simulator** uses `sim/sim_host.py` (sockets) instead of Linux ptys, so `.\rov.cmd run-sim` works without socat.

## Updating

The packaged app offers updates itself (button in the app). From source: `.\rov.cmd update`. Details and the
maintainer's release steps are in the main README, section 5c.
