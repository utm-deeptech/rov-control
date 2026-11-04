/* What each board port (blackpill/, bluepill/) provides. Pinout (same on both boards):
 *   UART  USART1  PA9 = TX -> Pi GPIO15 (RXD),  PA10 = RX <- Pi GPIO14 (TXD),  GND <-> GND
 *   Outputs 1..6 (thrusters)  PA6 PA7 PB0 PB1 PB6 PB7   50 Hz servo PWM, 1500 us = stop
 *   Lights (all 4 on one signal)  PB8                     1100 us = off ... 1900 us = full
 *   Camera tilt servo             PB9                     GMB_MIN_US .. GMB_MAX_US
 *   LED   PC13 (active low)
 *   Reserved for sensors: I2C2 SCL PB10 / SDA PB3 (Bar30 depth MS5837, IMU), battery ADC PA0/PA1, leak PA4
 */
#ifndef BOARD_H
#define BOARD_H
#include <stdint.h>

void     board_init(void);                     /* clocks, outputs at neutral, UART, 1 kHz tick, watchdog */
void     board_outputs(const uint16_t *us);    /* N_OUTPUTS pulse widths: 6 thrusters, lights, gimbal */
void     board_led(int on);
int      board_uart_read(uint8_t *dst, int max);       /* non-blocking: bytes received since last call */
int      board_uart_write(const uint8_t *p, int n);    /* non-blocking: returns bytes accepted (0 if busy) */
void     board_kick_watchdog(void);
uint32_t board_cycles(void);                   /* CPU cycle counter */
uint32_t board_cycles_per_us(void);
void     board_idle(void);                     /* sleep until the next interrupt */
/* + flash for parameters: see params.h */

/* implemented in common/app.c, called from the board's interrupts */
void app_tick_1ms(void);
#endif
