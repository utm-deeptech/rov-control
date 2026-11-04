#include "pid.h"

static float clampf(float x, float lim) { return x > lim ? lim : x < -lim ? -lim : x; }

void pidc_reset(pidc_t *c) { c->i = c->p_term = c->i_term = c->d_term = c->out = 0; }

float pidc_step(pidc_t *c, float err, float rate, float dt,
                float kp, float ki, float kd, float imax, float omax)
{
    float p = kp * err, d = -kd * rate;
    float unsat = p + c->i + d;
    int pushing_further = (unsat > omax && err > 0) || (unsat < -omax && err < 0);
    if (ki > 0 && !pushing_further)
        c->i = clampf(c->i + ki * err * dt, imax);
    if (ki <= 0) c->i = 0;
    c->p_term = p; c->i_term = c->i; c->d_term = d;
    c->out = clampf(p + c->i + d, omax);
    return c->out;
}
