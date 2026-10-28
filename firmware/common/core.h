/* ROV link protocol v1 — hardware-independent core (see protocol/PROTOCOL.md).
 * No register access here, so it is unit-tested on the laptop against protocol/rovproto.py. */
#ifndef CORE_H
#define CORE_H
#include <stdint.h>

#define N_THRUSTERS   6
#define FAILSAFE_MS   500u
#define TELEM_MS      100u
#define PWM_CENTER    1500
#define PWM_MIN       1100
#define PWM_MAX       1900
#define LIGHTS_OFF_US 1100
#define LIGHTS_FULL_US 1900

void     core_init(void);
void     core_rx(uint8_t byte, uint32_t now_ms);      /* feed one UART byte */
void     core_tick(uint32_t now_ms);                  /* failsafe + 10 Hz telemetry; call often */
int      core_tx_take(uint8_t *dst, int max);         /* bytes queued for the UART; returns count */
uint16_t core_pwm(int i);                             /* thruster pulse width (us) to output now */
uint16_t core_lights(void);                           /* lights pulse width (us) to output now */
int      core_armed(void);
int      core_failsafe(void);
uint16_t core_rx_ok(void);
uint16_t core_rx_bad(void);
int      core_linked(uint32_t now_ms);                /* valid frame seen within FAILSAFE_MS */
uint16_t core_crc16(const uint8_t *d, int n);
#endif
