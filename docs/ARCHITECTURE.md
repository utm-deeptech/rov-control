# ROV control architecture: who does what

Reference design: the BlueROV2. There, ArduSub on the flight controller owns the vehicle, the Pi is a
companion computer, and QGroundControl on the laptop is the pilot interface. We copy that split, with a
Black Pill in place of the flight controller and our own small protocol in place of MAVLink.

```
 PS4 / PS3 / AT9S ──> LAPTOP (topside)  ──UDP over FXTI──>  PI (bridge)  ──UART 460800──>  BLACK PILL (vehicle)
                      what the pilot WANTS                  a wire, plus video           how the vehicle DOES it
```

## The rule

**Anything needed to keep the vehicle safe and controllable goes on the Black Pill. Anything about the person
or the controller stays topside.**

So if the tether, the Pi or the laptop dies, the board still does the right thing (stop). And swapping a PS4
for an RC transmitter never touches the safety-critical code.

| | Topside (laptop) | Pi | Black Pill |
|---|---|---|---|
| Read controller (PS4 / PS3 / AT9S) | ✅ | | |
| Deadzone, expo, **pilot gain** | ✅ | | |
| Button → function mapping | ✅ (config/system.yaml) | | |
| Video, recording, logging | ✅ | ✅ camera | |
| Parameter editor (PID, mixing…) | ✅ UI | | ✅ storage + rules |
| UDP ⇄ UART | | ✅ only this | |
| Arming, pre-arm checks | requests | | ✅ decides |
| Failsafe (link lost → disarm, neutral) | | | ✅ |
| Modes: MANUAL / STABILIZE / DEPTH HOLD | requests | | ✅ decides (refuses without sensor) |
| **PID** (depth, heading, levelling) | | | ✅ 100 Hz |
| **Thruster mixing** (6 DOF → T1..T6) | | | ✅ |
| Thrust limit, slew rate, reverse, pin map | | | ✅ parameters |
| PWM: 6 ESCs, lights, **camera tilt servo** | | | ✅ |
| Sensors (IMU, depth, battery, leak) | display | | ✅ (drivers: next step) |

### Why the PID and the mixing have to be on the board

- **PID needs fresh sensor data at a steady rate.** The depth sensor and IMU are on the vehicle. A loop
  that goes through the tether (sensor → Pi → laptop → Pi → ESC) adds 20–250 ms of variable delay (measured on
  the bench over Wi-Fi). Variable delay makes PID loops oscillate.
- **The PID output is a DOF demand (e.g. "heave +0.3"), and it has to be added to the pilot's demand
  _before_ mixing.** So the mixer must sit after the PID, on the board.
- **Failsafe actions are DOF-level too** (stop now; later maybe "hold depth" or "surface"), so the board has to
  be able to drive the thrusters by itself.

### Why button mapping stays topside (not like ArduSub's `BTNn_FUNCTION`)

ArduSub maps buttons on the flight controller because MAVLink sends raw buttons. We have three very
different inputs: a PS4 and a PS3 (buttons + sticks) and an AT9S (sticks + 2/3-position switches, no buttons).
If the topside turns them into **requests** (arm, mode, lights level, camera angle), the board only ever sees
one format: the `PILOT` frame. Controller profiles live in one YAML file, and the board doesn't care which
controller is in your hands.

## Black Pill firmware (`firmware/`)

```
common/proto.c    framing + CRC (two parsers: UART and the SWD bench link)
common/core.c     the vehicle: arming, failsafe, modes, holds, mixing, PWM values, telemetry, params messages
common/pid.c      PID: derivative on measurement (no kick on setpoint change), anti-windup
common/mixer.c    6-DOF → 6 thrusters, ArduSub "vectored" factors by default, scales all together when saturated
common/params.c   ~70 parameters in flash: append-only records, CRC, survives power cuts
common/sim.c      bench plant model (make SIM=1): fake depth/IMU so the holds can be tried with no sensors
common/app.c      main loop (board side)
blackpill/board.c registers: clocks, DMA UART, PWM timers, flash, watchdog
```

All of `common/` except `app.c` is plain C with no registers, so it is compiled on the laptop and tested
against the Python reference (`firmware/test_core.py`, 28 tests including closed-loop PID tests against the
plant model).

### Bare metal + DMA + one interrupt, not FreeRTOS: why

| Job | Rate | How |
|---|---|---|
| UART receive | 46 kB/s max | DMA2 stream 2 into a 1 KB circular buffer. No interrupt per byte |
| UART transmit | ~1.5 kB/s | DMA2 stream 7, one transfer per batch of frames |
| Time base | 1 kHz | SysTick, the **only** interrupt |
| Parse, failsafe, telemetry | every 1 ms | main loop, then `WFI` (sleep) until the next tick |
| PID + mixer + PWM update | 100 Hz | `core_tick` every 10th ms |
| PWM generation | 50 Hz | hardware timers, preloaded (no glitches) |
| Watchdog | 250 ms | IWDG: a stuck loop resets the chip, and the ESCs see no pulse → stop |

Measured on the F411: **loop 13 µs average, 240 µs worst case, CPU 1.4 %**. With three jobs that each finish
in microseconds, an RTOS would add task stacks, priorities and locks around shared state (where most of the
bugs come from) without changing anything you can measure. The STATUS telemetry reports loop time and load,
so you can check this claim at any time. Revisit FreeRTOS when blocking drivers arrive (e.g. an IMU that
needs a 20 ms calibration sequence, or a microSD log). Even then, I²C sensors on DMA fit this loop fine.

### Modes

| Mode | Needs | Behaviour |
|---|---|---|
| DIRECT | — | protocol v2: the laptop sends per-thruster values (the current app, the thruster bench test) |
| MANUAL | — | pilot DOFs → mixer |
| STABILIZE | IMU | keeps roll/pitch level; heading hold when the yaw stick is released |
| DEPTH HOLD | depth (+IMU for heading) | + depth hold when the heave stick is released |

If the sensor a mode needs is missing or fails, the board runs MANUAL and reports it
(`mode` ≠ `mode_req` in STATUS). Holds lock **when the stick is released and the motion has stopped**
(< 5 °/s, < 5 cm/s), so they don't snap back to where you let go.

### Safety rules in the board

- Boot: disarmed, all ESCs 1500 µs. The watchdog is running.
- Pre-arm (pilot modes): sticks centred, thruster outputs a valid 1..6 permutation. Otherwise ARM is refused,
  with the reason in STATUS (still ACKed, so the app stops resending).
- No valid frame for `FS_TIMEOUT_MS` (500) while armed → disarm, neutral, `failsafe=1`. It never re-arms by itself.
- No PILOT frame for `PILOT_TMO_MS` (300) → sticks treated as centred (holds keep working).
- Thruster commands are slew-limited (`THR_SLEW`), which is easier on the ESCs and the tether power.
- Parameters that move thrusters (matrix, reverse, pin map) can't change while armed; PID gains can (live tuning).
- Flash save only while disarmed (erasing stalls the CPU ~1 s; the watchdog is stretched for it).

## Thrusters: BlueROV2 vectored frame

```
        FRONT
   T2 ⟋      ⟍ T1        T1 front-right   T2 front-left    (horizontal, 45°)
  T6 ◯        ◯ T5       T3 rear-right    T4 rear-left
   T4 ⟍      ⟋ T3        T5 vertical right  T6 vertical left
```

Default factors = ArduSub `SUB_FRAME_VECTORED`. Bench procedure (props off, then in a bucket):
1. DIRECT mode + the app's thruster test: check that each output spins the thruster you think. Fix with
   `THR_OUT_Tn`.
2. MANUAL, gain 25 %: push forward. Any thruster pushing the wrong way → set `THR_REV_Tn = 1`. Repeat for
   sway, heave and yaw.
3. Save to flash.

## Controllers

| | How it reaches the laptop | Notes |
|---|---|---|
| PS4 | USB or Bluetooth, SDL GameController | works now (topside/gamepad.py, rumble) |
| PS3 | USB (Bluetooth needs sixpair), SDL GameController | same names, same mapping |
| AT9S (RC) | **receiver at the surface** → SBUS → USB-joystick adapter, or the AT9S trainer port + a USB sim dongle | RC radio doesn't go through water: the receiver stays topside. Appears as a plain joystick, so it needs an axis/switch profile (3-pos switch → mode, a knob → camera tilt). Next step |

## Pins (Black Pill)

| Function | Pins |
|---|---|
| UART to Pi | PA9 TX, PA10 RX |
| Thrusters outputs 1–6 | PA6 PA7 PB0 PB1 PB6 PB7 (TIM3, TIM4) |
| Lights | PB8 (TIM4 CH3) |
| **Camera tilt servo** | **PB9 (TIM4 CH4)**, new |
| Reserved: I²C2 sensors (Bar30 MS5837, IMU) | PB10 SCL, PB3 SDA |
| Reserved: battery V/I, leak | PA0, PA1 (ADC), PA4 |

## Next steps

1. Reconnect the UART wires and switch the bench from SWD to UART (`jetson_bench.sh start uart`).
2. Depth sensor (Bar30 / MS5837 on I²C2) and IMU drivers → `core_set_sensors()`. The PID code is already tested.
3. Port the desktop app to v3 (PILOT + parameter page). Until then it keeps working in DIRECT mode.
4. AT9S input profile.
5. Battery voltage and leak sensor: telemetry + failsafe (leak → surface/alarm).
