/* Vehicle core. Data flow every 10 ms (core_tick -> control_step):
 *
 *   PILOT frame ──> pilot[6] ──┬──────────────────────────────┐
 *   (topside, 50 Hz)           │ stick released + mode needs it│
 *                              v                               v
 *   sensors ──> depth / heading / roll / pitch PID ──> dof[6] demand ──> mixer ──> slew ──> reverse/map ──> PWM
 *
 * DIRECT mode (protocol v2, the current laptop app) bypasses all of it: THRUSTERS values go straight to PWM.
 */
#include "core.h"
#include "proto.h"
#include "params.h"
#include "pid.h"
#include "mixer.h"
#ifdef BENCH_SIM
#include "sim.h"
#endif

#define MSG_HEARTBEAT   0x01
#define MSG_ARM         0x02
#define MSG_THRUSTERS   0x03
#define MSG_LIGHTS      0x04
#define MSG_PILOT       0x05
#define MSG_PARAM_GET   0x06
#define MSG_PARAM_SET   0x07
#define MSG_PARAM_SAVE  0x08
#define MSG_PARAM_RESET 0x09
#define MSG_TELEMETRY   0x81
#define MSG_ACK         0x82
#define MSG_STATUS      0x83
#define MSG_PARAM_VALUE 0x84
#define MSG_RESULT      0x85

#define RES_OK 0
#define RES_DENIED_ARMED 1
#define RES_FLASH 2
#define RES_BAD_ARG 3

enum { F_DEPTH_OK = 1, F_IMU_OK = 2, F_DEPTH_HOLD = 4, F_HDG_HOLD = 8, F_SIM = 16, F_DIRTY = 32,
       F_LOADED = 64, F_PILOT_STALE = 128 };

/* ---- link ---- */
static parser_t  rxp[2];
static uint8_t   txbuf[512];
static int       txn;
static uint8_t   tx_seq;
static uint16_t  rx_ok, rx_bad;
static uint32_t  last_link, last_telem, last_status, last_ctrl, last_pilot, last_stream;
static int       ever_linked, pilot_seen;
static int       stream_idx = -1;

/* ---- vehicle state ---- */
static int       armed, failsafe, arm_block;
static int       mode, mode_req;
static uint16_t  out_us[N_OUTPUTS];
static float     pilot[6];                    /* -1..1 surge sway heave roll pitch yaw */
static float     dof_final[6];
static float     thr_cmd[N_THRUSTERS];        /* after mixer + slew, before reverse, -1..1 */
static int16_t   gimbal_cmd;                  /* -1000..1000 */
static float     gimbal_us;
static sensors_t sens;
static float     depth_sp, hdg_sp;
static int       depth_locked, hdg_locked;
static pidc_t    pid_depth, pid_hdg, pid_roll, pid_pitch;
static int       params_loaded, params_dirty;
static uint16_t  t_loop, t_loop_max, t_load;

uint16_t core_crc16(const uint8_t *d, int n) { return proto_crc16(d, n); }

static float absf(float x) { return x < 0 ? -x : x; }
static float clamp1(float x) { return x > 1 ? 1 : x < -1 ? -1 : x; }

static void thrusters_neutral(void)
{
    for (int i = 0; i < N_THRUSTERS; i++) { out_us[i] = PWM_CENTER; thr_cmd[i] = 0; }
}

static void holds_reset(void)
{
    pidc_reset(&pid_depth); pidc_reset(&pid_hdg); pidc_reset(&pid_roll); pidc_reset(&pid_pitch);
    depth_locked = hdg_locked = 0;
    depth_sp = sens.depth; hdg_sp = sens.heading;
}

void core_init(void)
{
    parser_init(&rxp[0]); parser_init(&rxp[1]);
    txn = 0; tx_seq = 0; rx_ok = rx_bad = 0;
    last_link = last_telem = last_status = last_ctrl = last_pilot = last_stream = 0;
    ever_linked = pilot_seen = 0; stream_idx = -1;
    armed = failsafe = 0; arm_block = ARM_OK;
    mode = mode_req = MODE_DIRECT;
    for (int i = 0; i < 6; i++) pilot[i] = dof_final[i] = 0;
    thrusters_neutral();
    out_us[OUT_LIGHTS] = LIGHTS_OFF_US;
    params_loaded = params_load();
    params_dirty = 0;
    gimbal_cmd = 0;
    gimbal_us = P(GMB_CTR_US);
    out_us[OUT_GIMBAL] = (uint16_t)gimbal_us;
    for (unsigned i = 0; i < sizeof sens; i++) ((uint8_t *)&sens)[i] = 0;
#ifdef BENCH_SIM
    sim_init();
    sim_read(&sens);
#endif
    holds_reset();
    t_loop = t_loop_max = t_load = 0;
}

void core_set_sensors(const sensors_t *s)
{
#ifndef BENCH_SIM
    sens = *s;
#else
    (void)s;
#endif
}

void core_set_timing(uint16_t a, uint16_t b, uint16_t c) { t_loop = a; t_loop_max = b; t_load = c; }

/* ---------------- transmit ---------------- */
static int tx_free(void) { return (int)sizeof txbuf - txn; }

static void send(uint8_t id, const uint8_t *payload, int len)
{
    if (tx_free() < PROTO_OVERHEAD + len) return;               /* never block the control loop */
    txn += proto_encode(&txbuf[txn], id, ++tx_seq, payload, len);
}

int core_tx_take(uint8_t *dst, int max)
{
    int n = 0;
    while (n < txn) {                                           /* whole frames only */
        int fl = PROTO_OVERHEAD + txbuf[n + 2];
        if (n + fl > max) break;
        n += fl;
    }
    for (int i = 0; i < n; i++) dst[i] = txbuf[i];
    for (int i = n; i < txn; i++) txbuf[i - n] = txbuf[i];
    txn -= n;
    return n;
}

static void send_telemetry(uint32_t now)
{
    uint8_t p[12 + 2 * N_THRUSTERS];
    put32(p, now); p[4] = (uint8_t)armed; p[5] = (uint8_t)failsafe;
    put16(p + 6, rx_ok); put16(p + 8, rx_bad); put16(p + 10, out_us[OUT_LIGHTS]);
    for (int i = 0; i < N_THRUSTERS; i++) put16(p + 12 + 2 * i, out_us[i]);
    send(MSG_TELEMETRY, p, sizeof p);
}

static int16_t milli(float x) { x *= 1000.0f; return (int16_t)(x > 32767 ? 32767 : x < -32767 ? -32767 : x); }

static void send_status(uint32_t now)
{
    uint8_t p[60];
    int fl = (sens.depth_ok ? F_DEPTH_OK : 0) | (sens.imu_ok ? F_IMU_OK : 0)
           | (armed && depth_locked && mode == MODE_DEPTH_HOLD ? F_DEPTH_HOLD : 0)
           | (armed && hdg_locked && mode >= MODE_STABILIZE ? F_HDG_HOLD : 0)
           | (params_dirty ? F_DIRTY : 0) | (params_loaded ? F_LOADED : 0)
           | (pilot_seen && (uint32_t)(now - last_pilot) > (uint32_t)P(PILOT_TMO_MS) ? F_PILOT_STALE : 0);
#ifdef BENCH_SIM
    fl |= F_SIM;
#endif
    p[0] = (uint8_t)mode; p[1] = (uint8_t)mode_req; p[2] = (uint8_t)fl; p[3] = (uint8_t)arm_block;
    put16(p + 4, t_loop); put16(p + 6, t_loop_max); put16(p + 8, t_load); put16(p + 10, out_us[OUT_GIMBAL]);
    putf(p + 12, sens.depth); putf(p + 16, depth_sp); putf(p + 20, sens.heading); putf(p + 24, hdg_sp);
    putf(p + 28, sens.roll); putf(p + 32, sens.pitch);
    for (int i = 0; i < 6; i++) put16(p + 36 + 2 * i, (uint16_t)milli(dof_final[i]));
    put16(p + 48, (uint16_t)milli(pid_depth.p_term)); put16(p + 50, (uint16_t)milli(pid_depth.i_term));
    put16(p + 52, (uint16_t)milli(pid_depth.d_term));
    put16(p + 54, (uint16_t)milli(pid_hdg.p_term));   put16(p + 56, (uint16_t)milli(pid_hdg.i_term));
    put16(p + 58, (uint16_t)milli(pid_hdg.d_term));
    send(MSG_STATUS, p, sizeof p);
}

static void send_param(int i)
{
    uint8_t p[36];
    const param_info_t *pi = &param_info[i];
    p[0] = (uint8_t)i; p[1] = PARAM_COUNT; p[2] = pi->flags; p[3] = 0;
    putf(p + 4, param_v[i]); putf(p + 8, pi->def); putf(p + 12, pi->min); putf(p + 16, pi->max);
    int k = 0;
    for (; k < 16 && pi->name[k]; k++) p[20 + k] = (uint8_t)pi->name[k];
    for (; k < 16; k++) p[20 + k] = 0;
    send(MSG_PARAM_VALUE, p, sizeof p);
}

static void send_result(uint8_t id, uint8_t seq, uint8_t code)
{
    uint8_t p[3] = { id, seq, code };
    send(MSG_RESULT, p, 3);
}

/* ---------------- receive ---------------- */
static uint16_t cmd_to_pwm(int v)
{
    if (v > 1000) v = 1000;
    if (v < -1000) v = -1000;
    int x = v * 400;
    int q = x / 1000;                       /* floor like python's // */
    if (x % 1000 < 0) q--;
    q += PWM_CENTER;
    return (uint16_t)(q < PWM_MIN ? PWM_MIN : q > PWM_MAX ? PWM_MAX : q);
}

static int sticks_centered(void)
{
    for (int i = 0; i < 6; i++) if (absf(pilot[i]) > 0.1f) return 0;
    return 1;
}

static void handle(const frame_t *f, uint32_t now)
{
    const uint8_t *p = f->p;
    int len = f->len;
    switch (f->id) {
    case MSG_HEARTBEAT:
        if (len != 4) { rx_bad++; return; }
        break;
    case MSG_ARM:
        if (len != 1) { rx_bad++; return; }
        if (p[0] && !armed) {
            arm_block = ARM_OK;
            if (mode_req != MODE_DIRECT && !mixer_outputs_ok()) arm_block = ARM_BLOCK_OUTPUTS;
            else if (mode_req != MODE_DIRECT && !sticks_centered()) arm_block = ARM_BLOCK_STICKS;
            if (arm_block == ARM_OK) {
                thrusters_neutral();                /* stay at 1500 until the next command */
                holds_reset();
                armed = 1;
            }
        } else if (!p[0]) {
            armed = 0;
            thrusters_neutral();
        }                                           /* repeated ARM while armed: no change, no blip */
        failsafe = 0;
        { uint8_t a[2] = { MSG_ARM, f->seq }; send(MSG_ACK, a, 2); }
        break;
    case MSG_THRUSTERS:
        if (len != 2 * N_THRUSTERS) { rx_bad++; return; }
        if (armed && mode == MODE_DIRECT)
            for (int i = 0; i < N_THRUSTERS; i++) out_us[i] = cmd_to_pwm((int16_t)get16(p + 2 * i));
        break;
    case MSG_LIGHTS:                        /* lights work armed or disarmed, and survive failsafe */
        if (len != 2) { rx_bad++; return; }
        {
            int lv = get16(p);
            if (lv > 1000) lv = 1000;
            out_us[OUT_LIGHTS] = (uint16_t)(LIGHTS_OFF_US + lv * (LIGHTS_FULL_US - LIGHTS_OFF_US) / 1000);
        }
        break;
    case MSG_PILOT:                         /* u8 mode, u8 rsv, i16 dof[6], u16 lights, i16 gimbal */
        if (len != 18 || p[0] >= MODE_COUNT) { rx_bad++; return; }
        if (p[0] != mode_req && p[0] == MODE_DIRECT) thrusters_neutral();
        mode_req = p[0];
        for (int i = 0; i < 6; i++) {
            int v = (int16_t)get16(p + 2 + 2 * i);
            pilot[i] = clamp1((float)v / 1000.0f);
        }
        {
            int lv = get16(p + 14);
            if (lv > 1000) lv = 1000;
            out_us[OUT_LIGHTS] = (uint16_t)(LIGHTS_OFF_US + lv * (LIGHTS_FULL_US - LIGHTS_OFF_US) / 1000);
            int g = (int16_t)get16(p + 16);
            gimbal_cmd = (int16_t)(g > 1000 ? 1000 : g < -1000 ? -1000 : g);
        }
        last_pilot = now; pilot_seen = 1;
        break;
    case MSG_PARAM_GET:                     /* u8 index, 0xFF = all */
        if (len != 1) { rx_bad++; return; }
        if (p[0] == 0xFF) stream_idx = 0;
        else if (p[0] < PARAM_COUNT) send_param(p[0]);
        else send_result(f->id, f->seq, RES_BAD_ARG);
        break;
    case MSG_PARAM_SET:                     /* u8 index, f32 value */
        if (len != 5) { rx_bad++; return; }
        if (p[0] >= PARAM_COUNT) { send_result(f->id, f->seq, RES_BAD_ARG); break; }
        if (armed && !(param_info[p[0]].flags & PF_ARMED)) send_result(f->id, f->seq, RES_DENIED_ARMED);
        else if (params_set(p[0], getf(p + 1))) { params_dirty = 1; send_result(f->id, f->seq, RES_OK); }
        else send_result(f->id, f->seq, RES_BAD_ARG);
        send_param(p[0]);                   /* always report the value actually in use */
        break;
    case MSG_PARAM_SAVE:                    /* u8 0x5A; disarmed only (flash write stalls the CPU) */
        if (len != 1) { rx_bad++; return; }
        if (p[0] != 0x5A) send_result(f->id, f->seq, RES_BAD_ARG);
        else if (armed) send_result(f->id, f->seq, RES_DENIED_ARMED);
        else if (params_save()) send_result(f->id, f->seq, RES_FLASH);
        else { params_dirty = 0; params_loaded = 1; send_result(f->id, f->seq, RES_OK); }
        break;
    case MSG_PARAM_RESET:                   /* u8 0x5A: defaults in RAM (SAVE to keep them) */
        if (len != 1) { rx_bad++; return; }
        if (p[0] != 0x5A) send_result(f->id, f->seq, RES_BAD_ARG);
        else if (armed) send_result(f->id, f->seq, RES_DENIED_ARMED);
        else { params_defaults(); params_dirty = 1; stream_idx = 0; send_result(f->id, f->seq, RES_OK); }
        break;
    default:
        rx_bad++;                           /* unknown id: bad, and must not reset the link timer */
        return;
    }
    rx_ok++;
    last_link = now;
    ever_linked = 1;
}

void core_rx_port(int port, uint8_t b, uint32_t now)
{
    parser_t *ps = &rxp[port ? 1 : 0];
    uint16_t before = ps->bad;
    frame_t f;
    parser_push(ps, b);
    while (parser_pop(ps, &f)) handle(&f, now);
    rx_bad = (uint16_t)(rx_bad + (uint16_t)(ps->bad - before));
}

void core_rx(uint8_t b, uint32_t now) { core_rx_port(0, b, now); }

/* ---------------- control ---------------- */
static float wrap180(float a)
{
    while (a > 180) a -= 360;
    while (a < -180) a += 360;
    return a;
}

static int effective_mode(void)
{
    int m = mode_req;
    if (m == MODE_DEPTH_HOLD && !sens.depth_ok) m = MODE_MANUAL;    /* refuse a hold we can't do */
    if (m == MODE_STABILIZE && !sens.imu_ok) m = MODE_MANUAL;
    return m;
}

static void control_step(uint32_t now, float dt)
{
#ifdef BENCH_SIM
    sim_read(&sens);
#endif
    int m = effective_mode();
    if (m != mode) { mode = m; holds_reset(); if (mode == MODE_DIRECT) thrusters_neutral(); }

    /* camera tilt: slews toward the commanded angle, armed or not */
    {
        float ctr = P(GMB_CTR_US);
        float tgt = gimbal_cmd >= 0 ? ctr + gimbal_cmd * (P(GMB_MAX_US) - ctr) / 1000.0f
                                    : ctr + gimbal_cmd * (ctr - P(GMB_MIN_US)) / 1000.0f;
        float step = P(GMB_RATE) * dt;
        if (gimbal_us < tgt - step) gimbal_us += step;
        else if (gimbal_us > tgt + step) gimbal_us -= step;
        else gimbal_us = tgt;
        out_us[OUT_GIMBAL] = (uint16_t)(gimbal_us + 0.5f);
    }

    float u[6] = { 0, 0, 0, 0, 0, 0 };
    if (!armed || mode == MODE_DIRECT) {
        for (int i = 0; i < 6; i++) dof_final[i] = 0;
        if (!armed) { thrusters_neutral(); holds_reset(); }
    } else {
        float dz = P(PILOT_DZ);
        int stale = (uint32_t)(now - last_pilot) > (uint32_t)P(PILOT_TMO_MS);
        for (int i = 0; i < 6; i++) u[i] = stale ? 0 : pilot[i];

        if (mode >= MODE_STABILIZE && sens.imu_ok) {
            if (absf(u[3]) < dz)
                u[3] = pidc_step(&pid_roll, -sens.roll, sens.roll_rate, dt,
                                 P(ROLL_KP), 0, P(ROLL_KD), 0, P(ROLL_OMAX));
            if (absf(u[4]) < dz)
                u[4] = pidc_step(&pid_pitch, -sens.pitch, sens.pitch_rate, dt,
                                 P(PITCH_KP), 0, P(PITCH_KD), 0, P(PITCH_OMAX));
            if (absf(u[5]) >= dz) {
                hdg_locked = 0;                               /* pilot is turning: follow */
                hdg_sp = sens.heading;
            } else {
                if (!hdg_locked) {                            /* released: wait for the turn to stop */
                    hdg_sp = sens.heading;
                    if (absf(sens.yaw_rate) < 5.0f) hdg_locked = 1;
                }
                u[5] = pidc_step(&pid_hdg, wrap180(hdg_sp - sens.heading), sens.yaw_rate, dt,
                                 P(HDG_KP), P(HDG_KI), P(HDG_KD), P(HDG_IMAX), P(HDG_OMAX));
            }
        }
        if (mode == MODE_DEPTH_HOLD && sens.depth_ok) {
            if (absf(u[2]) >= dz) {
                depth_locked = 0;                             /* pilot is climbing / diving: follow */
                depth_sp = sens.depth;
            } else {
                if (!depth_locked) {
                    depth_sp = sens.depth;
                    if (absf(sens.depth_rate) < 0.05f) depth_locked = 1;
                }
                if (depth_sp < 0) depth_sp = 0;
                /* deeper than target -> err > 0 -> heave up (+). Upward speed is -depth_rate. */
                u[2] = pidc_step(&pid_depth, sens.depth - depth_sp, -sens.depth_rate, dt,
                                 P(DEPTH_KP), P(DEPTH_KI), P(DEPTH_KD), P(DEPTH_IMAX), P(DEPTH_OMAX));
            }
        }
        for (int i = 0; i < 6; i++) dof_final[i] = u[i] = clamp1(u[i]);

        float tgt[N_THRUSTERS];
        mixer_mix(u, tgt);
        float step = P(THR_SLEW) * dt;
        for (int t = 0; t < N_THRUSTERS; t++) {
            float d = tgt[t] - thr_cmd[t];
            thr_cmd[t] += d > step ? step : d < -step ? -step : d;
            float w = param_v[P_THR_REV_T1 + t] > 0.5f ? -thr_cmd[t] : thr_cmd[t];
            out_us[mixer_output_of(t)] = cmd_to_pwm((int)(w * 1000.0f + (w >= 0 ? 0.5f : -0.5f)));
        }
    }
#ifdef BENCH_SIM
    float ach[6] = { 0, 0, 0, 0, 0, 0 };
    if (armed && mode != MODE_DIRECT) mixer_achieved(thr_cmd, ach);
    sim_step(ach, dt);
#endif
}

void core_tick(uint32_t now)
{
    if (armed && (uint32_t)(now - last_link) > (uint32_t)P(FS_TIMEOUT_MS)) {
        armed = 0;
        failsafe = 1;
        thrusters_neutral();
    }
    uint32_t el = now - last_ctrl;
    if (el >= CTRL_MS) {
        last_ctrl = now;
        float dt = (float)el / 1000.0f;
        control_step(now, dt > 0.05f ? 0.05f : dt);
    }
    if (stream_idx >= 0 && (uint32_t)(now - last_stream) >= 10u && tx_free() >= 2 * (PROTO_OVERHEAD + 36)) {
        /* 100 params/s = 4.3 KB/s: fits the UART and the SWD bench link with room for telemetry */
        last_stream = now;
        send_param(stream_idx++);
        if (stream_idx >= PARAM_COUNT) stream_idx = -1;
    }
    if ((uint32_t)(now - last_telem) >= TELEM_MS) { last_telem = now; send_telemetry(now); }
    if ((uint32_t)(now - last_status) >= STATUS_MS) { last_status = now; send_status(now); }
}

uint16_t core_pwm(int i)      { return out_us[i]; }
uint16_t core_lights(void)    { return out_us[OUT_LIGHTS]; }
uint16_t core_gimbal(void)    { return out_us[OUT_GIMBAL]; }
uint16_t core_rx_ok(void)     { return rx_ok; }
uint16_t core_rx_bad(void)    { return rx_bad; }
int      core_armed(void)     { return armed; }
int      core_failsafe(void)  { return failsafe; }
int      core_mode(void)      { return mode; }
int      core_linked(uint32_t now) { return ever_linked && (uint32_t)(now - last_link) <= (uint32_t)P(FS_TIMEOUT_MS); }
