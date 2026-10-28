/* What each board port (blackpill/, bluepill/) provides. Same pinout on both boards:
 *   UART  USART1  PA9 = TX -> Pi GPIO15 (RXD),  PA10 = RX <- Pi GPIO14 (TXD),  GND <-> GND
 *   Thrusters FL FR RL RR VL VR = PA6 PA7 PB0 PB1 PB6 PB7   (50 Hz servo PWM, 1500 us = stop)
 *   Lights (all 4 on one signal)  = PB8                      (1100 us = off ... 1900 us = full)
 *   LED   PC13 (active low)
 */
#ifndef BOARD_H
#define BOARD_H
#include <stdint.h>

void board_init(void);                                   /* clocks, outputs at neutral, UART, 1 kHz tick, watchdog */
void board_outputs(const uint16_t *thr_us, uint16_t lights_us);
void board_led(int on);
void board_uart_write(const uint8_t *p, int n);
void board_kick_watchdog(void);

/* implemented in common/app.c, called from the board's interrupts */
void app_tick_1ms(void);
void app_rx_byte(uint8_t b);
#endif
