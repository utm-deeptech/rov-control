# ROV Link Protocol v3 — contract for the Black Pill firmware

The reference firmware implementing this is in `firmware/` (`common/core.c`, tested against `protocol/rovproto.py`).
v2 added LIGHTS. **v3** adds PILOT (the board mixes + runs the PID holds), STATUS, and parameters —
see [v3 additions](#v3-additions). Every v2 message is unchanged, so a v2 laptop still works (DIRECT mode).

The laptop and the Blackpill exchange identical binary frames. The laptop sends them over UDP to the
Raspberry Pi, and the Pi copies them byte for byte onto the UART (and the reverse for telemetry).
The Pi does **not** interpret control frames; the firmware is the final authority on safety.

Reference implementation: `protocol/rovproto.py`. Reference behaviour: `sim/fake_blackpill.py`.
If this document and the simulator ever disagree, this document wins — please report it.

## Physical link

| Item | Value |
|---|---|
| UART | 460800 baud, 8N1, no flow control (configurable in `config/system.yaml`) |
| Pi side | GPIO14 (TXD) → Blackpill RX, GPIO15 (RXD) ← Blackpill TX, common GND, 3.3 V logic |
| Black Pill (STM32F401/F411) | USART1: PA9 = TX → Pi GPIO15, PA10 = RX ← Pi GPIO14, GND ↔ GND |
| Thruster outputs FL FR RL RR VL VR | PA6 PA7 PB0 PB1 PB6 PB7 (TIM3 CH1-4, TIM4 CH1-2), 50 Hz |
| Lights output (all 4 lights) | PB8 (TIM4 CH3), 50 Hz, 1100 µs off … 1900 µs full |
| Camera tilt servo (v3) | PB9 (TIM4 CH4), 50 Hz, `GMB_MIN_US`..`GMB_MAX_US` |
| Status LED | PC13: on = armed, linked & disarmed = on while lights are on (else off), fast blink = failsafe, short flash = no link |

## Frame format

All multi-byte fields are **little-endian**.

```
offset  size  field
0       1     0xAA   sync 1
1       1     0x55   sync 2
2       1     len    payload length in bytes (0..64)
3       1     msg_id
4       1     seq    sender's rolling counter 0..255 (wraps)
5       len   payload
5+len   2     crc16  CRC16-CCITT over bytes [2 .. 5+len-1] (len, msg_id, seq, payload)
```

CRC: CRC-16/CCITT-FALSE — polynomial 0x1021, init 0xFFFF, no input/output reflection, no final XOR.
Check value: `crc("123456789") == 0x29B1`. Transmitted low byte first.

```c
uint16_t crc16_ccitt(const uint8_t *d, size_t n) {
    uint16_t crc = 0xFFFF;
    while (n--) {
        crc ^= (uint16_t)(*d++) << 8;
        for (int i = 0; i < 8; i++)
            crc = (crc & 0x8000) ? (crc << 1) ^ 0x1021 : (crc << 1);
    }
    return crc;
}
```

### Receiver rules (both ends)

1. Hunt for `0xAA 0x55`. Bytes before it are discarded.
2. If `len > 64`: discard the first sync byte and hunt again (counts as a bad frame).
3. Wait for the full frame. If the CRC is wrong: discard **one** byte and hunt again
   (a real frame may start inside the corrupted one). Counts as a bad frame.
4. A frame with a valid CRC but an unknown `msg_id` or wrong payload length for its type is dropped
   and counted as bad. It must not reset the link timer.

## Messages

| ID | Name | Direction | Payload | Rate |
|----|------|-----------|---------|------|
| 0x01 | HEARTBEAT | laptop → BP | `u32 laptop_ms` | 10 Hz |
| 0x02 | ARM | laptop → BP | `u8 armed` (1 = arm, 0 = disarm) | on demand, resent until ACKed |
| 0x03 | THRUSTERS | laptop → BP | `N × i16` command, −1000..1000 | 50 Hz (sent while disarmed too, as zeros) |
| 0x04 | LIGHTS | laptop → BP | `u16 level` 0..1000 (0 = off, >1000 clamped) | 10 Hz (with HEARTBEAT) |
| 0x81 | TELEMETRY | BP → laptop | `u32 uptime_ms, u8 armed, u8 failsafe, u16 rx_ok, u16 rx_bad, u16 lights_us, N × u16 pwm_us` | 10 Hz |
| 0x82 | ACK | BP → laptop | `u8 acked_msg_id, u8 acked_seq` | once per ARM received |

`N` is the thruster count (default **6**: FL, FR, RL, RR, VL, VR — order matters, it is the output order).
A THRUSTERS frame whose `N` differs from the firmware's thruster count is dropped and counted as bad.

TELEMETRY is 12 + 2N bytes (24 bytes for N = 6). `rx_ok` / `rx_bad` are free-running u16 counters of valid /
rejected frames received on the UART (wrap at 65535).

## Required behaviour

1. **Boot:** disarmed, `failsafe = 0`, every output at **1500 µs** (ESC neutral). Start sending TELEMETRY.
2. **Disarmed:** outputs are forced to 1500 µs regardless of THRUSTERS contents.
3. **Armed:** for each thruster `pwm = 1500 + value * 400 / 1000` (integer math, truncate toward −∞
   or toward zero — either is fine, ±1 µs), then clamp to **1100..1900 µs**.
4. **ARM handling:** on every valid ARM frame, set `armed` to the payload, clear `failsafe`, reset the link
   timer, and reply with `ACK(acked_msg_id=0x02, acked_seq=<seq of that ARM>)`. When arming from
   disarmed, outputs stay at 1500 until the next THRUSTERS frame arrives; a repeated ARM while already armed changes
   nothing (still ACKed). When disarming, outputs go to 1500 immediately.
5. **Link timer:** reset by every valid HEARTBEAT, THRUSTERS or LIGHTS frame (and ARM, see 4).
6. **Failsafe:** if armed and no valid HEARTBEAT/THRUSTERS for **500 ms**: set all outputs to 1500 µs,
   disarm, set `failsafe = 1`. Stay disarmed until a new ARM frame arrives (the laptop never re-arms
   automatically). `failsafe` stays 1 in telemetry until then so the operator sees why it disarmed.
7. Telemetry `pwm_us` / `lights_us` report the pulse widths actually being output right now.
8. **Lights:** boot at 1100 µs (off). `lights_us = 1100 + level * 800 / 1000`. Lights work armed or disarmed and
   are NOT changed by failsafe, so the ROV stays visible.
9. ESC outputs: 50–400 Hz standard servo PWM; 1500 µs = stop. Hold 1500 µs for ≥ 2 s after power-up so the
   ESCs initialise (the boot state already guarantees this).

## Timing budget

Laptop sends THRUSTERS every 20 ms and HEARTBEAT every 100 ms, so in normal operation the link timer never
exceeds ~20 ms. The 500 ms failsafe therefore tolerates ~25 consecutive lost THRUSTERS frames.

## Example frames

```
ARM armed=1, seq=7          AA 55 01 02 07 01 <crc lo> <crc hi>
HEARTBEAT 1000 ms, seq=0    AA 55 04 01 00 E8 03 00 00 <crc lo> <crc hi>
```
Generate exact bytes (with CRC) for unit tests with:
`python3 -c "import sys; sys.path.insert(0,'protocol'); import rovproto as r; print(r.arm(7,1).hex(' '))"`

## v3 additions

Laptop → board:

| ID | Name | Payload | Rate |
|----|------|---------|------|
| 0x05 | PILOT | `u8 mode, u8 0, 6 × i16 dof (-1000..1000: surge sway heave roll pitch yaw), u16 lights 0..1000, i16 tilt -1000..1000` (18 B) | 50 Hz |
| 0x06 | PARAM_GET | `u8 index` (0xFF = all, streamed 100/s) | on demand |
| 0x07 | PARAM_SET | `u8 index, f32 value` | on demand → RESULT + PARAM_VALUE |
| 0x08 | PARAM_SAVE | `u8 0x5A` (disarmed only) | on demand → RESULT |
| 0x09 | PARAM_RESET | `u8 0x5A` defaults in RAM (disarmed only) | on demand → RESULT |

Board → laptop:

| ID | Name | Payload |
|----|------|---------|
| 0x83 | STATUS (20 Hz) | `u8 mode, u8 mode_req, u8 flags, u8 arm_block, u16 loop_us, u16 loop_max_us, u16 load_permille, u16 tilt_us, f32 depth, depth_sp, heading, heading_sp, roll, pitch, 6 × i16 dof demand ×1000, 3 × i16 depth P/I/D ×1000, 3 × i16 heading P/I/D ×1000` (60 B) |
| 0x84 | PARAM_VALUE | `u8 index, u8 count, u8 flags (1 = changeable armed, 2 = integer), u8 0, f32 value, default, min, max, char name[16]` (36 B) |
| 0x85 | RESULT | `u8 msg_id, u8 seq, u8 code` — 0 ok, 1 not while armed, 2 flash error, 3 bad argument |

Modes: 0 DIRECT (v2 THRUSTERS drive the outputs, PILOT DOFs ignored), 1 MANUAL, 2 STABILIZE (IMU), 3 DEPTH_HOLD
(depth sensor). The board boots in DIRECT. PILOT carries the *requested* mode at every frame; the board runs it
if it has the sensors, else MANUAL (STATUS `mode` vs `mode_req`). THRUSTERS frames are ignored outside DIRECT.

STATUS flags: bit0 depth_ok, 1 imu_ok, 2 holding depth, 3 holding heading, 4 SIM (plant model, not real sensors),
5 params changed since save, 6 params loaded from flash, 7 PILOT stream stale.

Extra rules (v3):
- ARM in a pilot mode is refused (still ACKed) unless all PILOT DOFs are within ±100 and THR_OUT_T1..T6 is a
  permutation of 1..6. `arm_block`: 1 sticks, 2 outputs.
- No PILOT for `PILOT_TMO_MS` → DOFs treated as 0. The failsafe timer (`FS_TIMEOUT_MS`) is reset by any valid frame.
- Board mixing: `thr = M · dof`, all scaled down together if any |thr| > 1, × THR_LIMIT, slew-limited by
  THR_SLEW per second, × −1 if THR_REV_Tn, written to output THR_OUT_Tn. Same mixing as `topside/mixer.py`.
- Parameters live in flash sector 5 (0x08020000) as append-only CRC'd records; a damaged last record falls back
  to the previous one; a firmware with a different parameter table loads defaults.
