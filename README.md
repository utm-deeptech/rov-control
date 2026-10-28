# ROV Control

Laptop app + Raspberry Pi bridge + Black Pill firmware for a 6-thruster ROV with one lights channel, live video and
PS3 / PS4 / PS5 / Xbox controller support.

```
 Laptop (app, controller)  ──FXTI tether (Ethernet) or Wi-Fi──  Raspberry Pi 4  ──UART──  Black Pill  ──PWM──  6 ESCs + lights
        ▲  video (H.264/RTP)  ◄──────────────────────────────  camera
```

* **Safety is enforced by the Black Pill firmware** (C, `firmware/`): outputs start at neutral, thrusters run only
  when armed, a 500 ms link-loss failsafe sets thrusters to 1500 µs and disarms (no automatic re-arm), and a 250 ms
  hardware watchdog resets the board if its program stops.
* **The Pi needs no network-specific configuration**: it takes the laptop's address from incoming control packets
  and sends the video to that address, over the tether or Wi-Fi.
* **The laptop** needs the Pi's IP address only: select **TETHER** or **WI-FI** in the app's Link menu.

## How it works

### Laptop

At 50 Hz the app reads the controller and the mixer (`thrusters.mixing`) converts the requested motion
(forward, sideways, up/down, turn) into six thruster commands, one per thruster, in the range -1000 to +1000. The
power limit, reverse settings and output mapping (which pin each thruster uses) are applied here. The app sends the
commands to the Pi together with a heartbeat (10 Hz), the lights level (10 Hz) and ARM/DISARM requests.

### Raspberry Pi

`pi/bridge.py` forwards bytes between the network (UDP) and the UART without interpreting them. The Pi also runs
the camera and streams the video to the laptop that is sending the control packets.

### Black Pill

The firmware has no notion of manoeuvres such as "forward". It:

* receives messages and verifies their checksums (invalid messages are discarded),
* converts each thruster command to a pulse width, `1500 + value × 0.4` µs (limited to 1100–1900 µs), on that
  output's pin, only while armed,
* drives the lights output (1100 µs off to 1900 µs full),
* returns telemetry at 10 Hz: armed state, the pulse width on every output, and message counters,
* sets all thrusters to neutral if no valid message arrives for 500 ms (failsafe) or if its program stops (watchdog).

Example, right stick fully forward at 50 % power: the laptop sends `FL=+500 FR=+500 RL=+500 RR=+500 VL=0 VR=0`, and
the Black Pill outputs 1700 µs on the four horizontal thrusters and 1500 µs (stop) on the two vertical thrusters.
Combined motions produce different commands in the same format. Changes to how the ROV responds to the controls are
made in the laptop configuration and do not require reflashing the firmware.

### Arming and disarming

**Disarmed** (default at power-on): every thruster is held at 1500 µs (stop) regardless of the commands received.
**Armed**: the board outputs the commands it receives.

To arm, press ARM (app button, or Options / Start):

1. The app refuses to arm unless the sticks are centred. The first time, it asks for confirmation that the props are clear.
2. The app sends ARM every 0.1 s until the board acknowledges it. If there is no acknowledgement within 1 s, it reports that there is no link.
3. The board arms and keeps all outputs at 1500 µs until the next thruster command. The LED stays on and the button changes to DISARM.
4. The app sends zero commands until the board's telemetry reports that it is armed.

The board disarms (all thrusters to 1500 µs immediately) when you press SPACE, Share / Select, ○ / B or the DISARM
button; when the controller disconnects or the app closes; when you switch link, reset or flash; and on
**failsafe**, when no valid message arrives for 0.5 s. After a failsafe the board does not re-arm automatically; press ARM again. The lights are
not affected by arming or failsafe.

## 1. Laptop (Ubuntu 22.04+), once

```bash
git clone https://github.com/utm-deeptech/rov-control.git ~/rov-control
cd ~/rov-control
make setup        # apt packages (asks sudo) + .venv with pinned versions
make test         # all green
make run-sim      # whole system on the laptop, no hardware
make run          # the real thing
make desktop      # adds "ROV Control" to the applications menu
```

## 2. Raspberry Pi (on the ROV), once

Use **Raspberry Pi OS Lite 64-bit (Bookworm)**: the camera tools (`rpicam-apps`) are packaged there. Ubuntu works
for the control link but needs `rpicam-apps` built from source for video.

Give the Pi internet (Wi-Fi) for the first install, then on the Pi:

```bash
git clone https://github.com/utm-deeptech/rov-control.git ~/rov
cd ~/rov && bash pi/install.sh      # packages, UART, eth0 = 192.168.2.2, rov-bridge service
sudo reboot                          # needed once (UART config)
```

`install.sh` is safe to re-run. Updating later: `cd ~/rov && git pull && bash pi/install.sh --no-apt`.

What it sets up:
* UART on GPIO14/15 as the full PL011 (`dtoverlay=disable-bt`, Bluetooth off), login console removed.
* `eth0` static `192.168.2.2/24` with no gateway (Wi-Fi keeps the internet). Skip with `--no-eth`.
* `rov-bridge` service: UDP ↔ UART bridge + camera, restarts on failure, starts at boot.

## 3. FXTI tether

| Side | Address | How |
|---|---|---|
| Pi `eth0` | `192.168.2.2/24` | done by `pi/install.sh` |
| Laptop FXTI USB-Ethernet | `192.168.2.1/24` | `make network` (pick the `enx...` interface, once) |

Then in the app choose **Link → TETHER · 192.168.2.2**. Your laptop's Wi-Fi internet keeps working. To use other
addresses change `network:` in `config/system.yaml` (and re-run `pi/install.sh` on the Pi).

Without the tether (bench): put the Pi on Wi-Fi, choose **Link → Set Wi-Fi Pi IP…** and enter the address from
`hostname -I` on the Pi. It's saved in `config/local.yaml` (per laptop, not in git).

## 4. Black Pill firmware

Same pinout on STM32F401CC and STM32F411CE Black Pills (one binary for both):

| Signal | Black Pill pin | Goes to |
|---|---|---|
| UART TX | PA9 | Pi GPIO15 / pin 10 (RXD) |
| UART RX | PA10 | Pi GPIO14 / pin 8 (TXD) |
| GND | G | Pi GND (pin 6) — **must** be common |
| Outputs 1-6 (thrusters) | PA6 PA7 PB0 PB1 PB6 PB7 | ESC signal wires, any order |
| Lights (all 4) | PB8 | lights signal (1100 µs off … 1900 µs full) |
| LED | PC13 | on = armed · off = linked/disarmed · fast blink = failsafe · flash every 2 s = no link |

Which thruster is on which output depends on the configuration. Set it in the app: **Thrusters → SETUP** (output /
pin and reverse for each thruster; picking a used output swaps the two). It applies immediately, is locked while
armed, and is saved in `config/rov.yaml` (in git, so it travels with the repo). Default FL→1, FR→2, RL→3, RR→4,
VL→5, VR→6. Plug the ESCs in any order, no reflash.
The 6 output pins themselves are fixed in the firmware (`firmware/*/board.c`), since they must be timer pins.

Flash it (prebuilt binaries are in `firmware/prebuilt/`, or build with `make fw`):

* **USB-C (no extra hardware):** hold BOOT0, tap NRST, release BOOT0, then `make fw-dfu`.
* **ST-LINK on the laptop:** `make fw-flash`.
* **ST-LINK on the Pi:** the app's **Flash firmware…** button (pick `firmware/prebuilt/blackpill.bin`).

The protocol contract is `protocol/PROTOCOL.md`.

## 5. Driving

| Action | Controller (PS / Xbox) | Keyboard |
|---|---|---|
| Up / down | left stick ↕ | R / F |
| Turn | left stick ↔ | Q / E |
| Forward / back | right stick ↕ | W / S |
| Sideways | right stick ↔ | A / D |
| **ARM** | Options / Start | ARM button |
| **DISARM** | Share / Select or ○ / B | **SPACE** (always) |
| Power limit 25/50/75/100 % | L1 / R1 | [ / ] |
| Lights brighter / dimmer | D-pad ↑ / ↓ | = / - |
| Lights on / off | △ / Y | L |

* Arming asks for confirmation once, and refuses while sticks are off-centre.
* Disarm happens automatically when the controller disconnects, the app closes, or the link drops (board failsafe).
* **Bench test:** in the Thrusters card click a thruster (FL…VR), arm, then hold the slider (max 30 %, springs back to
  0). Use it with props off to check each thruster's direction; flip wrong ones with the Reverse tick in Thrusters → SETUP.
* The mixing matrix and stick layout are in `config/system.yaml` (`thrusters.mixing`, `gamepad.axes`).

**Is the controller connected?** The header shows a green **● PS4 CONTROLLER** chip (grey **NO CONTROLLER** when
not), and the pad rumbles when it connects, arms and disarms. The panel under the video shows both sticks, triggers
and buttons live, plus what the sticks are telling the ROV (FORWARD / SIDEWAYS / UP-DOWN / TURN). While disarmed
the thruster bars show a dim preview of what each thruster would do. Unplugging or losing the controller disarms.

Controllers: PS4/PS5 pair over Bluetooth (hold Share + PS until the light flashes) or plug in USB. PS3 works over
USB (Bluetooth needs `sixpair`). Check what the app sees with `make gamepad-test`.

## 6. Bench mode without UART wires (Blue Pill / ST-LINK only)

If the board is connected to the Pi **only** by an ST-LINK, `make pi-swd` runs `pi/swd_bridge.py` on the Pi: it feeds
the same protocol into the firmware's RAM over SWD, so the app works unchanged (LINK OK, arming, lights, PWM readback).
Set `firmware: board: bluepill` in the Pi's `config/local.yaml` for a Blue Pill. This is for bench testing only.

## Layout

```
config/      system.yaml (defaults) + local.yaml (per machine, git-ignored)
firmware/    common/ (protocol core + main loop), blackpill/, bluepill/, prebuilt/
pi/          bridge.py (UART), swd_bridge.py (bench), camera.sh, camera_supervisor.py, install.sh, setup_eth.sh
protocol/    PROTOCOL.md, rovproto.py (reference implementation)
topside/     the app (PySide6), control link, controller, video
sim/         fake board + end-to-end tests; make run-sim
```

## Troubleshooting

* **No video but LINK OK:** another copy of the app may be holding port 5600: close it (`pkill -f topside/app.py`).
  On the Pi: `journalctl -u rov-bridge -f` and `tail /tmp/rov_camera.log`.
* **LINK LOST:** check the Link menu IP, `ping` the Pi, and on the Pi `journalctl -u rov-bridge -f`
  (`serial=up`, `ser_frames` should increase).
* **RX BAD climbing:** UART wiring/ground, or the Pi is still on the mini-UART (re-run `pi/install.sh`, reboot).

## Status (2026-10-28)

Verified:
* Full chain on a bench Pi 4 over Wi-Fi: app → Pi → **Blue Pill** (via ST-LINK/SWD, no UART wires): arm/disarm with
  ACK, LED, telemetry, PWM readback, live 720p30 video in the app.
* 35 automated tests: protocol, firmware core (compiled for the laptop and checked against `rovproto.py`), mixer,
  controller config, end-to-end sim (arming, PWM mapping, lights, failsafe, no auto re-arm).

Not yet verified on hardware (do these first on the real ROV, props off):
1. Flash `firmware/prebuilt/blackpill.bin` to the Black Pill, wire the UART (table above), check the app shows
   LINK OK and `RX BAD` stays 0.
2. `make gamepad-test` with your PS3/PS4 pad: sticks/buttons as in the Driving table.
3. Bench test each thruster; fix direction in `thrusters.reverse`, layout in `thrusters.mixing`.
4. Lights: 50 % should read back 1500 µs and the lights should be half brightness.
5. FXTI: `make network` on the laptop, Link → TETHER, video + LINK OK.
