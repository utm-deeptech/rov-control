#include "sim.h"

static float depth, vz, hdg, r, roll, roll_r, pitch, pitch_r;

void sim_init(void)
{
    depth = 2.0f; vz = 0; hdg = 90.0f; r = 0; roll = pitch = roll_r = pitch_r = 0;
}

void sim_step(const float u[6], float dt)
{
    /* heave: +u = up. 0.6 m/s^2 at full, drag 1.2/s -> 0.5 m/s top speed; net weight sinks 2.5 cm/s */
    vz += (-0.6f * u[2] + 0.03f - 1.2f * vz) * dt;
    depth += vz * dt;
    if (depth < 0) { depth = 0; if (vz < 0) vz = 0; }          /* surface */
    if (depth > 30) { depth = 30; if (vz > 0) vz = 0; }        /* bottom */
    /* yaw: 100 deg/s top speed */
    r += (300.0f * u[5] - 3.0f * r) * dt;
    hdg += r * dt;
    while (hdg >= 360) hdg -= 360;
    while (hdg < 0) hdg += 360;
    /* roll / pitch: bottom-heavy, so it self-rights; a small roll bias the stabiliser has to fight */
    roll_r += (200.0f * u[3] + 4.0f - 4.0f * roll_r - 8.0f * roll) * dt;
    roll += roll_r * dt;
    pitch_r += (200.0f * u[4] - 4.0f * pitch_r - 8.0f * pitch) * dt;
    pitch += pitch_r * dt;
}

void sim_read(sensors_t *s)
{
    s->depth_ok = s->imu_ok = 1;
    s->depth = depth; s->depth_rate = vz;
    s->heading = hdg; s->yaw_rate = r;
    s->roll = roll; s->roll_rate = roll_r;
    s->pitch = pitch; s->pitch_rate = pitch_r;
}
