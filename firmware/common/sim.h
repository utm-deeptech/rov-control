/* Bench plant model (build with `make SIM=1` only). Gives the PID loops something to control on the bench
 * when no IMU / depth sensor is fitted: a slightly heavy, bottom-heavy ROV that sinks ~3 cm/s when idle.
 * Telemetry flags every frame with SIM so it can never be mistaken for a real sensor.
 */
#ifndef SIM_H
#define SIM_H
#include "core.h"

void sim_init(void);
void sim_step(const float dof_achieved[6], float dt);
void sim_read(sensors_t *s);
#endif
