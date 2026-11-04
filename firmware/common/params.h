/* Vehicle parameters: one table, stored in flash, read/written over the link (PARAM_GET / SET / SAVE).
 *
 * X(name, default, min, max, flags)   flags: PF_ARMED = may be changed while armed (live tuning)
 *                                            PF_INT   = whole number (shown without decimals)
 * Thrusters T1..T6 use the BlueROV2 / ArduSub "vectored" numbering:
 *   T1 front-right  T2 front-left  T3 rear-right  T4 rear-left   (horizontal, 45 deg)
 *   T5 vertical right             T6 vertical left
 * Mixing columns: surge (+fwd), sway (+right), heave (+up), roll (+right side down), pitch (+nose up), yaw (+clockwise)
 * Default factors = ArduSub SUB_FRAME_VECTORED. Prop handedness / ESC wiring differ per build:
 * fix a thruster that pushes the wrong way with THR_REV_Tn, never by editing the matrix.
 */
#ifndef PARAMS_H
#define PARAMS_H
#include <stdint.h>

#define PF_ARMED 1u
#define PF_INT   2u

#define PARAM_LIST(X) \
    X(FS_TIMEOUT_MS, 500,  200, 2000, PF_INT)  /* link lost this long while armed -> disarm */ \
    X(PILOT_TMO_MS,  300,  100, 2000, PF_INT)  /* no PILOT frame this long -> pilot sticks = 0 */ \
    X(PILOT_DZ,     0.05f, 0,   0.5f, PF_ARMED)/* stick below this = "released" -> hold engages */ \
    X(THR_LIMIT,    1.0f,  0.1f, 1.0f, PF_ARMED)/* max thruster command (1 = full) */ \
    X(THR_SLEW,     4.0f,  0.5f, 50,  PF_ARMED)/* max change per second (4 = 0->full in 0.25 s) */ \
    X(DEPTH_KP,     2.0f,  0,   20,   PF_ARMED)/* heave per metre of depth error */ \
    X(DEPTH_KI,     0.4f,  0,   10,   PF_ARMED) \
    X(DEPTH_KD,     1.5f,  0,   20,   PF_ARMED)/* heave per m/s of vertical speed */ \
    X(DEPTH_IMAX,   0.5f,  0,   1,    PF_ARMED)/* integrator limit (holds buoyancy offset) */ \
    X(DEPTH_OMAX,   1.0f,  0,   1,    PF_ARMED) \
    X(HDG_KP,       0.02f, 0,   1,    PF_ARMED)/* yaw per degree of heading error */ \
    X(HDG_KI,       0.0f,  0,   1,    PF_ARMED) \
    X(HDG_KD,       0.01f, 0,   1,    PF_ARMED)/* yaw per deg/s of turn rate */ \
    X(HDG_IMAX,     0.2f,  0,   1,    PF_ARMED) \
    X(HDG_OMAX,     0.6f,  0,   1,    PF_ARMED) \
    X(ROLL_KP,      0.02f, 0,   1,    PF_ARMED)/* level the vehicle in STABILIZE / DEPTH_HOLD */ \
    X(ROLL_KD,      0.005f,0,   1,    PF_ARMED) \
    X(ROLL_OMAX,    0.5f,  0,   1,    PF_ARMED) \
    X(PITCH_KP,     0.02f, 0,   1,    PF_ARMED) \
    X(PITCH_KD,     0.005f,0,   1,    PF_ARMED) \
    X(PITCH_OMAX,   0.5f,  0,   1,    PF_ARMED) \
    X(GMB_MIN_US,   1100,  800, 1500, PF_INT|PF_ARMED) /* camera tilt servo: full down */ \
    X(GMB_CTR_US,   1500,  1000,2000, PF_INT|PF_ARMED) \
    X(GMB_MAX_US,   1900,  1500,2200, PF_INT|PF_ARMED) /* full up */ \
    X(GMB_RATE,     600,   50,  5000, PF_INT|PF_ARMED) /* servo slew, us per second */ \
    X(THR_OUT_T1, 1, 1, 6, PF_INT) /* board output (1..6 = PA6 PA7 PB0 PB1 PB6 PB7) for each thruster */ \
    X(THR_OUT_T2, 2, 1, 6, PF_INT) \
    X(THR_OUT_T3, 3, 1, 6, PF_INT) \
    X(THR_OUT_T4, 4, 1, 6, PF_INT) \
    X(THR_OUT_T5, 5, 1, 6, PF_INT) \
    X(THR_OUT_T6, 6, 1, 6, PF_INT) \
    X(THR_REV_T1, 0, 0, 1, PF_INT) /* 1 = this thruster pushes the wrong way: flip it */ \
    X(THR_REV_T2, 0, 0, 1, PF_INT) \
    X(THR_REV_T3, 0, 0, 1, PF_INT) \
    X(THR_REV_T4, 0, 0, 1, PF_INT) \
    X(THR_REV_T5, 0, 0, 1, PF_INT) \
    X(THR_REV_T6, 0, 0, 1, PF_INT) \
    /*         surge        sway         heave        roll         pitch        yaw   */ \
    MIXROW(T1, -1,           1,           0,           0,           0,           1) \
    MIXROW(T2, -1,          -1,           0,           0,           0,          -1) \
    MIXROW(T3,  1,           1,           0,           0,           0,          -1) \
    MIXROW(T4,  1,          -1,           0,           0,           0,           1) \
    MIXROW(T5,  0,           0,          -1,           1,           0,           0) \
    MIXROW(T6,  0,           0,          -1,          -1,           0,           0)

#define MIXROW(t, a, b, c, d, e, f) \
    X(MIX_##t##_SURGE, a, -1, 1, 0) X(MIX_##t##_SWAY, b, -1, 1, 0) X(MIX_##t##_HEAVE, c, -1, 1, 0) \
    X(MIX_##t##_ROLL, d, -1, 1, 0)  X(MIX_##t##_PITCH, e, -1, 1, 0) X(MIX_##t##_YAW, f, -1, 1, 0)

enum {
#define X(n, d, lo, hi, fl) P_##n,
    PARAM_LIST(X)
#undef X
    PARAM_COUNT
};

typedef struct { const char *name; float def, min, max; uint8_t flags; } param_info_t;

extern float          param_v[PARAM_COUNT];
extern const param_info_t param_info[PARAM_COUNT];
#define P(n) (param_v[P_##n])

void     params_defaults(void);
int      params_load(void);                     /* 1 = loaded from flash, 0 = defaults */
int      params_save(void);                     /* 0 = ok, else error */
int      params_set(int i, float v);            /* clamps; returns 1 if accepted */
uint32_t params_hash(void);
int      params_saves_left(void);               /* free slots before the sector must be erased */

/* storage provided by the board (or host/flash_emu.c in tests). Word-addressed, erased = 0xFFFFFFFF. */
const volatile uint32_t *board_flash_base(void);
uint32_t board_flash_words(void);
int      board_flash_erase(void);
int      board_flash_write(uint32_t word_off, const uint32_t *w, uint32_t n);
#endif
