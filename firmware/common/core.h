/* Vehicle core — hardware-independent (no registers), unit-tested on the laptop (firmware/test_core.py).
 *
 * What the Black Pill owns (the "vehicle controller", like ArduSub on a BlueROV2 Navigator):
 *   link framing + failsafe + arming      every frame is CRC-checked; link loss while armed -> disarm, neutral
 *   modes                                 DIRECT (v2: raw per-thruster), MANUAL, STABILIZE, DEPTH_HOLD
 *   PID holds                             depth, heading, roll/pitch levelling — need sensors, else refused
 *   thruster mixing                       6-DOF -> T1..T6 (BlueROV2 vectored), limit, slew, reverse, output map
 *   PWM for 6 ESCs + lights + camera tilt
 *   parameters in flash                   PID gains, mixing, reverse, outputs… get/set/save over the link
 * What it does NOT own: which button does what, stick curves, pilot gain, video — that is the topside's job,
 * so the same firmware works with a PS3, PS4 or an RC transmitter.
 */
#ifndef CORE_H
#define CORE_H
#include <stdint.h>

#define N_THRUSTERS    6
#define N_OUTPUTS      8           /* 0..5 thrusters (board outputs 1..6), 6 = lights, 7 = camera tilt */
#define OUT_LIGHTS     6
#define OUT_GIMBAL     7
#define CTRL_MS        10u         /* control loop: 100 Hz */
#define TELEM_MS       100u        /* TELEMETRY (v2) 10 Hz */
#define STATUS_MS      50u         /* STATUS 20 Hz */
#define PWM_CENTER     1500
#define PWM_MIN        1100
#define PWM_MAX        1900
#define LIGHTS_OFF_US  1100
#define LIGHTS_FULL_US 1900

enum { MODE_DIRECT = 0, MODE_MANUAL = 1, MODE_STABILIZE = 2, MODE_DEPTH_HOLD = 3, MODE_COUNT };
enum { ARM_OK = 0, ARM_BLOCK_STICKS = 1, ARM_BLOCK_OUTPUTS = 2 };

typedef struct sensors_t {
    int   depth_ok, imu_ok;
    float depth, depth_rate;                 /* m (+down), m/s */
    float heading, yaw_rate;                 /* deg 0..360, deg/s (+clockwise) */
    float roll, roll_rate, pitch, pitch_rate;/* deg, deg/s (+right side down, +nose up) */
} sensors_t;

void     core_init(void);
void     core_rx(uint8_t byte, uint32_t now_ms);              /* UART byte */
void     core_rx_port(int port, uint8_t byte, uint32_t now_ms);/* 0 = UART, 1 = SWD mailbox (bench) */
void     core_tick(uint32_t now_ms);                          /* call every ms: control, failsafe, telemetry */
int      core_tx_take(uint8_t *dst, int max);                 /* whole frames only; returns bytes */
void     core_set_sensors(const sensors_t *s);                /* board: fresh sensor values (not in SIM builds) */
void     core_set_timing(uint16_t loop_us, uint16_t loop_max_us, uint16_t load_permille);

uint16_t core_pwm(int i);                                     /* thruster OUTPUT i pulse width (us) */
uint16_t core_lights(void);
uint16_t core_gimbal(void);
int      core_armed(void);
int      core_failsafe(void);
int      core_mode(void);
uint16_t core_rx_ok(void);
uint16_t core_rx_bad(void);
int      core_linked(uint32_t now_ms);
uint16_t core_crc16(const uint8_t *d, int n);
#endif
