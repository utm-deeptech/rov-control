/* 6-DOF demand -> 6 thruster commands, using the MIX_Tn_* parameters.
 * Same algorithm as topside/mixer.py: if any thruster would exceed full scale, ALL are scaled down together
 * so the direction of motion is kept (just slower). Then THR_LIMIT. (Reverse / output mapping: core.c.)
 */
#ifndef MIXER_H
#define MIXER_H

#define N_DOF 6          /* surge sway heave roll pitch yaw */
#define N_THR 6

void mixer_mix(const float dof[N_DOF], float thr[N_THR]);
/* what the thrusters actually produce in DOF space (least squares through the matrix): for the bench sim */
void mixer_achieved(const float thr[N_THR], float dof[N_DOF]);
int  mixer_outputs_ok(void);     /* THR_OUT_T1..T6 use each output exactly once */
int  mixer_output_of(int t);     /* 0-based board output for thruster t */
#endif
