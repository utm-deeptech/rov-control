# Handoff: state of the bench (2026-11-04)

Read this first if you are picking the ROV work up. It says what is physically on the Black Pill and the Jetson
right now, how to get the bench running, and what is still open. Design and reasons: [ARCHITECTURE.md](ARCHITECTURE.md).

Code: `utm-deeptech/rov-control`, branch **`fw-v3-vehicle-controller`** (not merged into `master` yet; `master` is
the older v2 code that drove the real FXTI + Pi setup).

## The pieces

```
laptop (app)  --Wi-Fi/LAN UDP-->  Jetson (stand-in for the ROV's Pi)  --ST-LINK/SWD-->  Black Pill F411
bench/station_app.py --bench       ~/rov-bench: pi/bridge.py + bench/swd_link.py          firmware/ (v3)
```

On the real ROV the Jetson is replaced by the Raspberry Pi, the link is the FXTI tether, and the Black Pill talks
to the Pi over UART instead of SWD. The code above the wire is the same.

## What is on the Black Pill (STM32F411, WeAct)

Checked 2026-11-04 by reading the flash back over the ST-LINK.

| | |
|---|---|
| Firmware | v3 vehicle controller, **SIM build** (`make SIM=1`): fake depth/IMU plant so the PID holds work with no sensors. Byte-identical to `~/rov-bench/firmware/build/blackpill-sim/rovfw.bin` on the Jetson (sha256 `42d24803…bc36601`). |
| Built from | firmware sources of commit `db07fef` (unchanged up to `ed2bf7c`), **before** the params fix below |
| Saved params (flash sector 5, 0x08020000) | 1 record. All defaults except **`DEPTH_KP = 2.5`** (default 2.0). |
| Sensors | none wired. Depth/attitude come from the SIM plant. |
| Thrusters / ESCs | none connected. Outputs PA6 PA7 PB0 PB1 PB6 PB7, lights PB8, camera tilt PB9 (see ARCHITECTURE.md § Pins). |
| UART (PA9/PA10) | wired to the Jetson's pin 10/8 (`/dev/ttyTHS1`) but **dead**: nothing gets through (checked 11-03 and 11-04). Suspect the wires/connector. That's why the bench uses SWD. |
| Power | from its USB-C. Unplugging the USB-C reboots it (the ST-LINK does not power it). |

### Bug found and fixed (not flashed yet)

`firmware/common/params.c`: a saved record was one word too short, so the CRC overwrote the **last** parameter
(`MIX_T6_YAW`). That parameter could never be saved. On boot the board saw an out-of-range value and used the
default (0). Fixed in the commit after `ed2bf7c` (`REC_WORDS = 5 + PARAM_COUNT`), with a test
(`test_every_param_survives_reboot_including_last`).

**The board still runs the old build.** After you flash the fix, the old saved record no longer matches the new
layout. The board then starts on defaults, so `DEPTH_KP` goes back to 2.0. Set it again and save if you want 2.5.

## What is on the Jetson

Orin Nano devkit, hostname `deeptech-jetson`, user `ahmed` (SSH key auth from the laptop; ask the owner for access).
Its IP changes with the network: **172.20.10.2 on the iPhone hotspot** (2026-11-04), 192.168.0.103 on the lab TP-Link.
It doesn't answer ping. Find it with `nmap -Pn -p22 --open <subnet>`.

| Path | What |
|---|---|
| `~/rov-bench/` | rsync copy of this repo (no `.git`). The firmware sources match the branch. The laptop-side app files there are older, which doesn't matter because the app runs on the laptop. **Before working: re-sync from the laptop** (command below). |
| `~/rov-bench/firmware/build/blackpill-sim/` | the build that is on the board (needed by `swd_link.py` to find the RAM link buffers) |
| `~/rov-bench/logs/` | `flashed` (= `sim`), `bridge.log`, `swd_link.log` |
| `~/blackpill-blink/` | **older, separate experiments** (UART/USB-CDC ping tests, `backup_before_blink.bin` = the board's original firmware). Not part of the ROV code. |
| `~/uart_*_test.py`, `~/libopencm3`, `~/ch341-build` | older experiments, not used by the bench |
| ST-LINK V2 | on a Jetson USB port, SWD to the Black Pill. `openocd` works without sudo (udev rule installed). |

Nothing starts by itself: no services, no cron. After a reboot the bench is off until you start it.

## Running the bench

```bash
# laptop, repo root: push your copy to the Jetson (keeps its build/ and logs/)
rsync -a --exclude .git --exclude .venv --exclude __pycache__ --exclude firmware/build --exclude logs \
      ./ jetson:rov-bench/

# Jetson, in ~/rov-bench
bash bench/jetson_bench.sh flash sim     # build + flash (sim = fake depth/IMU). `flash real` = no plant model
bash bench/jetson_bench.sh start swd     # bridge over SWD.  `start uart` once the UART wires work
bash bench/jetson_bench.sh status | logs | stop

# laptop
.venv/bin/python bench/station_app.py --bench 172.20.10.2     # desktop app (or the "ROV Bench" launcher)
.venv/bin/python bench/groundstation.py --vehicle 172.20.10.2  # browser-only version, http://localhost:8080
```

`config/system.yaml` → `network.bench_ip` still says `192.168.0.103` (lab LAN). Pass the IP on the command line
when the Jetson is somewhere else.

Gotchas:
- Stop the stations with `kill -9` (SDL catches SIGTERM). Safe: the board's failsafe disarms when the link drops.
- `jetson_bench.sh flash` stops the bridge first. Run `start` again afterwards.
- Laptop has no `arm-none-eabi-gcc`. Build the firmware on the Jetson (or install the toolchain).
- `make test` on the laptop runs all host tests (protocol, mixer, firmware core compiled for x86, end-to-end sim).

## Verified vs not

Verified on the real F411 over SWD: end-to-end link, arming/disarming, failsafe on link loss, depth hold against the
SIM plant, parameters saved to flash surviving a reset. The thruster mixing matches ArduSub `SUB_FRAME_VECTORED`.

Not done / not verified (in order):
1. **Fix the UART wires** and switch to `start uart`. The real ROV needs UART.
2. Flash the params fix (above) and re-check save/reboot.
3. Real sensors: Bar30 (MS5837) + IMU on I²C2 (PB10/PB3) → `core_set_sensors()`, then `flash real`.
4. Thrusters on the bench, **props off**: direction of each (fix with `THR_REV_Tn`, never by editing the matrix).
5. Desktop app parameter page / AT9S controller profile. The classic v2 app (`topside/app.py`) still works in DIRECT mode.
6. Battery voltage and leak sensor (PA0/PA1/PA4).
