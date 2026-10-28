/* Board-independent main loop: UART + SWD mailbox -> core, core -> outputs/LED/UART.
 *
 * LED PC13: ON = ARMED | OFF = linked, disarmed | fast blink = FAILSAFE | short flash every 2 s = no link
 * SWD mailbox (bench without UART wires): the Pi writes whole protocol frames into swd_in[] then swd_in_len;
 * they go through the same core_rx() as UART bytes. swd_out[] mirrors the state for the Pi to read.
 */
#include "board.h"
#include "core.h"

volatile uint8_t  swd_in[64];
volatile uint32_t swd_in_len;
volatile uint32_t swd_out[12];     /* uptime_ms, armed, failsafe, rx_ok, rx_bad, lights_us, thruster_us[6] */

static volatile uint32_t ms;
static volatile uint8_t rxq[256];
static volatile uint8_t rx_head, rx_tail;

void app_tick_1ms(void) { ms++; }

void app_rx_byte(uint8_t b)
{
    uint8_t next = (uint8_t)(rx_head + 1);
    if (next != rx_tail) { rxq[rx_head] = b; rx_head = next; }
}

static void led(uint32_t now)
{
    int on;
    if (core_armed())            on = 1;
    else if (core_failsafe())    on = (now / 100) & 1;
    else if (core_linked(now))   on = 0;
    else                         on = (now % 2000) < 100;
    board_led(on);
}

int main(void)
{
    core_init();
    board_init();
    uint16_t thr[N_THRUSTERS];

    for (;;) {
        board_kick_watchdog();
        uint32_t now = ms;
        while (rx_tail != rx_head) {
            uint8_t b = rxq[rx_tail];
            rx_tail = (uint8_t)(rx_tail + 1);
            core_rx(b, now);
        }
        uint32_t sn = swd_in_len;
        if (sn && sn <= sizeof swd_in) {
            for (uint32_t i = 0; i < sn; i++) core_rx(swd_in[i], now);
            swd_in_len = 0;
        }
        core_tick(now);
        for (int i = 0; i < N_THRUSTERS; i++) thr[i] = core_pwm(i);
        board_outputs(thr, core_lights());

        uint8_t out[64];
        int n;
        while ((n = core_tx_take(out, sizeof out)) > 0) board_uart_write(out, n);
        led(now);

        swd_out[0] = now; swd_out[1] = (uint32_t)core_armed(); swd_out[2] = (uint32_t)core_failsafe();
        swd_out[3] = core_rx_ok(); swd_out[4] = core_rx_bad(); swd_out[5] = core_lights();
        for (int i = 0; i < N_THRUSTERS; i++) swd_out[6 + i] = thr[i];
    }
}
