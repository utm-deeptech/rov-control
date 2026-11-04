/* PID for one axis.
 *   out = Kp*err + I - Kd*rate        (derivative on the MEASURED rate, so a setpoint jump never kicks)
 *   I  += Ki*err*dt, limited to +-imax, and frozen while the output is saturated in the same direction
 *        (anti-windup: the integrator can't pile up while the thrusters are already at their limit)
 */
#ifndef PID_H
#define PID_H

typedef struct {
    float i;              /* integrator state */
    float p_term, i_term, d_term, out;   /* last step, for telemetry */
} pidc_t;

void  pidc_reset(pidc_t *c);
float pidc_step(pidc_t *c, float err, float rate, float dt,
                float kp, float ki, float kd, float imax, float omax);
#endif
